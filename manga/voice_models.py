"""独立した音声設定。既存の人物ID・ストーリーJSONは変更しない。"""
import hashlib
import json
import math
import unicodedata
from uuid import UUID, uuid5

EMOTIONS = {'normal':'通常','angry':'怒り','sad':'悲しみ','whisper':'ささやき／小声'}
MOCK_VOICES = ('mock-1','mock-2','mock-3','mock-4')
# Stock voice IDs present in the official Polly SDK enum. Japanese/engine
# availability and commercial terms must still be reviewed before paid use.
POLLY_VOICES = ('Mizuki','Takumi','Kazuha','Tomoko')
POLLY_ENGINES = {'Mizuki':('standard',),'Takumi':('standard','neural'),'Kazuha':('neural',),'Tomoko':('neural',)}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def narrator_id(project_id):
    return str(uuid5(UUID(project_id),'voice:narrator'))


def speech_text(value):
    if not isinstance(value,str) or not value.strip() or len(value)>3000:
        raise ValueError('読み上げ文は1〜3000文字で指定してください。長文は発話を分割してください。')
    if any((unicodedata.category(c) in ('Cc','Cs','Co') and c not in '\n\t\r') or ord(c)&0xffff in (0xfffe,0xffff) for c in value):
        raise ValueError('読み上げ文に未対応の制御文字・私用文字があります。')
    return unicodedata.normalize('NFC',value.strip().replace('\r\n','\n').replace('\r','\n'))


def voice_settings(raw):
    provider = raw.get('provider','mock')
    engine = raw.get('engine','mock')
    voice = raw.get('voice_id','mock-1')
    if provider not in ('mock','aws-polly','voicevox','manual-wav') or not isinstance(voice,str) or not voice or len(voice)>80:
        raise ValueError('音声プロバイダー・声IDが不正です。')
    if provider=='mock' and (engine!='mock' or voice not in MOCK_VOICES):
        raise ValueError('確認用モックの声を選択してください。')
    if provider=='aws-polly' and (engine not in ('standard','neural') or voice not in POLLY_VOICES):
        raise ValueError('Pollyのengineと公式の声IDを確認してください。')
    if provider=='aws-polly' and engine not in POLLY_ENGINES[voice]:raise ValueError('この日本語の声IDは選択したengineに対応していません。')
    speed = raw.get('speed',1.0)
    if type(speed) not in (int,float) or not math.isfinite(speed) or not .5<=speed<=1.5:
        raise ValueError('速度は0.5〜1.5で指定してください。')
    emotion = raw.get('emotion','normal')
    if emotion not in EMOTIONS:
        raise ValueError('感情を選択してください。')
    result = dict(provider=provider,engine=engine,voice_id=voice,speed=float(speed),emotion=emotion)
    if provider=='manual-wav':
        if engine!='manual' or voice!='manual' or speed!=1 or emotion!='normal':
            raise ValueError('取り込みWAVは元の読み・速度を使います。声設定は手動取り込みを選択してください。')
    if provider=='voicevox':
        from manga.models import validate_id
        from manga.voicevox import ORIGINS
        validate_id(voice)
        if engine!='local' or raw.get('origin') not in ORIGINS or emotion!='normal':
            raise ValueError('VOICEVOXの許可済み接続先・通常感情を選択してください。演技はスタイルで指定します。')
        style=raw.get('style_id')
        if type(style) is not int or not 0<=style<=2**31-1:raise ValueError('VOICEVOXスタイルIDが不正です。')
        result.update(origin=raw['origin'],style_id=style)
        for key,default,low,high in (('pitch',0,-.15,.15),('intonation',1,0,2),('volume',1,0,2)):
            value=raw.get(key,default)
            if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
                raise ValueError('VOICEVOXの音高・抑揚・音量が設定範囲外です。')
            result[key]=float(value)
    return result


def cache_key(utterance,settings):
    return digest(dict(format='manga-voice-pcm-v1',speaker_id=utterance['speaker_id'],
        text=speech_text(utterance['spoken_text']),settings=voice_settings(settings),sample_rate=16000))


def empty_voice(project_id):
    return dict(schema_version=1,format='manga-voice-project',project_id=project_id,revision=0,
        settings_revision=0,voices={},edits={},pricing=None,jobs=[],assets=[],assignments={},audio_reviews={},timelines=[],events=[])
