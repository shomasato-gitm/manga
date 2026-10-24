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
    if provider not in ('mock','aws-polly') or not isinstance(voice,str) or not voice or len(voice)>80:
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
    return dict(provider=provider,engine=engine,voice_id=voice,speed=float(speed),emotion=emotion)


def cache_key(utterance,settings):
    return digest(dict(format='manga-voice-pcm-v1',speaker_id=utterance['speaker_id'],
        text=speech_text(utterance['spoken_text']),settings=voice_settings(settings),sample_rate=16000))


def empty_voice(project_id):
    return dict(schema_version=1,format='manga-voice-project',project_id=project_id,revision=0,
        settings_revision=0,voices={},edits={},pricing=None,jobs=[],assets=[],assignments={},audio_reviews={},timelines=[],events=[])
