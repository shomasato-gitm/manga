"""明示操作でのみ接続するローカルVOICEVOX。HTTPプロキシ・リダイレクトなし。"""
import http.client
import json
import os
import platform
from urllib.parse import urlencode

from manga.models import validate_id
from manga.voice_audio import MAX_WAV_BYTES, inspect_wav

ORIGINS = ('http://127.0.0.1:50021', 'http://[::1]:50021')
SOFTWARE_TERMS = 'https://voicevox.hiroshiba.jp/term/'
MAC_NOTICE = ('公式ソフトの現行案内はmacOS 14以降です。ENGINE 0.26.0にはIntel用x64版とMac署名の案内があり、'
              'ビルド変更はmacOS 13向けですが、macOS 12の動作は未確認です。動かない場合はWAV手動取り込みをご利用ください。')


def local_available():
    return platform.system() == 'Darwin' and os.environ.get('MANGA_ENABLE_LOCAL_VOICEVOX') == '1'


def _transport(origin, method, path, body, maximum):
    # http.client never reads HTTP_PROXY/ALL_PROXY, does not redirect or retry.
    if not local_available():
        raise ValueError('利用者のMacでローカル接続を明示的に有効にしてください。クラウドのlocalhostには接続しません。')
    host = '127.0.0.1' if origin == ORIGINS[0] else '::1'
    connection = http.client.HTTPConnection(host, 50021, timeout=30)
    try:
        connection.request(method, path, body=body, headers={'Content-Type':'application/json','Accept-Encoding':'identity'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('ローカルエンジンの応答を確認できません。転送・再送は行いません。')
        content = response.read(maximum+1)
        if len(content)>maximum:raise ValueError('ローカルエンジンの応答が容量上限を超えています。')
        return content
    except (OSError, http.client.HTTPException):
        raise ValueError('VOICEVOXに接続できません。Mac上のエンジンと接続先を確認するか、WAVを取り込んでください。') from None
    finally:
        connection.close()


def validate_speakers(value):
    try:
        if not isinstance(value,list) or not 1<=len(value)<=500:raise ValueError
        speakers=set();styles=set();result=[]
        for row in value:
            identifier=validate_id(row['speaker_uuid'])
            if identifier in speakers:raise ValueError
            speakers.add(identifier)
            if any(not isinstance(row[k],str) or not 1<=len(row[k])<=200 for k in ('name','version')):raise ValueError
            if not isinstance(row['styles'],list) or not 1<=len(row['styles'])<=200:raise ValueError
            accepted=[]
            for style in row['styles']:
                number=style['id']
                if type(number) is not int or not 0<=number<=2**31-1 or number in styles:raise ValueError
                styles.add(number)
                if not isinstance(style['name'],str) or not 1<=len(style['name'])<=200:raise ValueError
                if style.get('type','talk') in ('talk','streaming_talk'):
                    accepted.append(dict(id=number,name=style['name']))
            if accepted:result.append(dict(speaker_uuid=identifier,name=row['name'],version=row['version'],styles=accepted))
        if not result:raise ValueError
        return result
    except (KeyError,TypeError,ValueError):
        raise ValueError('VOICEVOXの話者一覧が不正です。UUID・スタイルの重複や対応する音声を確認してください。') from None


class Voicevox:
    def __init__(self, origin=ORIGINS[0], *, send=None):
        if origin not in ORIGINS:raise ValueError('許可したIPv4/IPv6ループバックだけに接続できます。')
        self.origin=origin
        self.send=send or _transport

    def _request(self, method, path, *, consent, query=None, body=None, wav=False):
        if consent is not True:raise ValueError('このMacのローカルエンジンへの接続を明示確認してください。')
        if (method,path) not in (('GET','/version'),('GET','/speakers'),('POST','/audio_query'),('POST','/synthesis')):
            raise ValueError('未対応のエンジン操作です。')
        if query:path+='?'+urlencode(query)
        content=self.send(self.origin,method,path,json.dumps(body,ensure_ascii=False,allow_nan=False).encode() if body is not None else None,
                          MAX_WAV_BYTES if wav else 2*1024*1024)
        if not isinstance(content,bytes) or len(content)>(MAX_WAV_BYTES if wav else 2*1024*1024):raise ValueError('エンジン応答の形式・容量が不正です。')
        if wav:return content
        try:return json.loads(content,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):raise ValueError('エンジンのJSON応答を読み込めません。') from None

    def discover(self, *, consent=False):
        version=self._request('GET','/version',consent=consent)
        if not isinstance(version,str) or not 1<=len(version)<=100:raise ValueError('エンジンの版が不正です。')
        speakers=validate_speakers(self._request('GET','/speakers',consent=consent))
        return dict(version=version,speakers=speakers,origin=self.origin)

    def synthesize(self, text, settings, *, consent=False):
        from manga.voice_models import speech_text,voice_settings
        settings=voice_settings(settings)
        if settings['provider']!='voicevox' or settings['origin']!=self.origin:raise ValueError('声設定とエンジン接続先が一致しません。')
        text=speech_text(text)
        catalog=self.discover(consent=consent)
        speaker=next((s for s in catalog['speakers'] if s['speaker_uuid']==settings['voice_id']),None)
        if not speaker or settings['style_id'] not in [s['id'] for s in speaker['styles']]:
            raise ValueError('保存した話者UUIDとスタイルがエンジンに一致しません。声設定を確認してください。')
        query=self._request('POST','/audio_query',consent=consent,query=dict(text=text,speaker=settings['style_id']))
        if not isinstance(query,dict) or not isinstance(query.get('accent_phrases'),list) or not query['accent_phrases']:
            raise ValueError('読み上げクエリに音声の内容がありません。')
        query.update(speedScale=settings['speed'],pitchScale=settings['pitch'],intonationScale=settings['intonation'],
                     volumeScale=settings['volume'],outputSamplingRate=16000,outputStereo=False)
        wav=self._request('POST','/synthesis',consent=consent,query=dict(speaker=settings['style_id']),body=query,wav=True)
        inspect_wav(wav)
        return wav,dict(engine_version=catalog['version'],library=speaker,origin=self.origin)
