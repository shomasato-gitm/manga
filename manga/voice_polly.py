"""Polly RESTアダプター。呼び出し元はBudgetedExecutor内に限定する。"""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from html import escape
from urllib.request import Request, build_opener, HTTPRedirectHandler

from manga.voice_audio import MAX_WAV_BYTES
from manga.voice_models import speech_text, voice_settings


class VoiceProviderError(ValueError):
    pass


def polly_ssml(text,settings):
    text=speech_text(text);settings=voice_settings(settings)
    rate,volume={'normal':(100,0),'angry':(110,2),'sad':(80,-3),'whisper':(90,-8)}[settings['emotion']]
    rate=round(rate*settings['speed'])
    # These are prosody presets, not a claim that neural voices support emotional styles.
    body=f'<prosody rate="{rate}%" volume="{volume:+d}dB">{escape(text)}</prosody>'
    if settings['engine']=='standard' and settings['emotion']=='whisper':
        body='<amazon:effect name="whispered">'+body+'</amazon:effect>'
    return '<speak>'+body+'</speak>'


def credentials():
    result={key:os.environ.get(key,'') for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN')}
    if not result['AWS_ACCESS_KEY_ID'] or not result['AWS_SECRET_ACCESS_KEY']:
        raise VoiceProviderError('AWSの認証情報を環境変数に設定してください。値を画面やログへ貼り付けないでください。')
    return result


def signed_request(payload,region,keys,*,now=None):
    if not re.fullmatch(r'[a-z]{2}-[a-z]+-\d',region):raise ValueError('AWSリージョンが不正です。')
    host=f'polly.{region}.amazonaws.com'
    timestamp=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    day=timestamp[:8]
    headers={'content-type':'application/json','host':host,'x-amz-date':timestamp}
    if keys.get('AWS_SESSION_TOKEN'):headers['x-amz-security-token']=keys['AWS_SESSION_TOKEN']
    names=';'.join(sorted(headers))
    canonical='POST\n/v1/speech\n\n'+''.join(f'{k}:{headers[k].strip()}\n' for k in sorted(headers))+'\n'+names+'\n'+hashlib.sha256(payload).hexdigest()
    scope=f'{day}/{region}/polly/aws4_request'
    to_sign='AWS4-HMAC-SHA256\n'+timestamp+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    key=('AWS4'+keys['AWS_SECRET_ACCESS_KEY']).encode()
    for part in (day,region,'polly','aws4_request'):key=hmac.new(key,part.encode(),hashlib.sha256).digest()
    signature=hmac.new(key,to_sign.encode(),hashlib.sha256).hexdigest()
    headers['authorization']=f"AWS4-HMAC-SHA256 Credential={keys['AWS_ACCESS_KEY_ID']}/{scope}, SignedHeaders={names}, Signature={signature}"
    return Request('https://'+host+'/v1/speech',data=payload,headers=headers,method='POST')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise VoiceProviderError('音声APIのリダイレクトを拒否しました。')


def transport(request):
    try:
        # urllib performs no automatic POST retries. Never log headers or raw errors.
        with build_opener(NoRedirect()).open(request,timeout=20) as response:
            deadline=time.monotonic()+45
            chunks=[];total=0
            while True:
                if time.monotonic()>deadline:raise VoiceProviderError('音声API応答の制限時間を超えました。')
                chunk=response.read1(min(65536,MAX_WAV_BYTES-total+1))
                if not chunk:break
                chunks.append(chunk);total+=len(chunk)
                if total>MAX_WAV_BYTES:raise VoiceProviderError('音声APIの容量上限を超えました。')
            body=b''.join(chunks)
            headers={k.lower():v for k,v in response.headers.items()}
            if len(body)>MAX_WAV_BYTES or headers.get('content-type','').split(';')[0]!='audio/pcm':
                raise VoiceProviderError('音声APIの出力形式・容量が不正です。')
            return body,headers
    except Exception:
        raise VoiceProviderError('音声APIの応答を安全に確認できません。再送せず請求状態を確認してください。') from None


class PollyVoice:
    def __init__(self,*,send=None,keys=None,region=None):
        self.send=send or transport
        self.keys=keys
        self.region=region or os.environ.get('AWS_REGION','ap-northeast-1')

    def synthesize(self,ssml,settings):
        payload=json.dumps(dict(Engine=settings['engine'],VoiceId=settings['voice_id'],LanguageCode='ja-JP',
                                OutputFormat='pcm',SampleRate='16000',TextType='ssml',Text=ssml),ensure_ascii=False,separators=(',',':')).encode()
        request=signed_request(payload,self.region,self.keys or credentials())
        try:
            pcm,headers=self.send(request)
            raw=headers.get('x-amzn-requestcharacters')
            count=int(raw) if raw is not None and re.fullmatch(r'\d{1,7}',str(raw)) else None
            return pcm,count
        except Exception:
            raise VoiceProviderError('音声提供元の処理結果が不明です。予約を保持し、明細の確認まで再送しません。') from None
