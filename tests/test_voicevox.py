"""HTTPはすべてフェイク。実エンジン・外部APIには接続しない。"""
import copy
import json
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4

import pytest

from manga.voice_audio import MockVoice
from manga.voice_models import voice_settings
from manga.voicevox import Voicevox,ORIGINS,validate_speakers,local_available

SPEAKER='11111111-1111-4111-8111-111111111111'
CATALOG=[dict(name='検証話者',speaker_uuid=SPEAKER,version='test',styles=[dict(name='通常',id=3,type='talk')])]


def settings(**fields):
    return voice_settings(dict(provider='voicevox',engine='local',voice_id=SPEAKER,origin=ORIGINS[0],style_id=3,**fields))


class EngineFake:
    def __init__(self):self.calls=[];self.catalog=copy.deepcopy(CATALOG)
    def __call__(self,origin,method,path,body,maximum):
        self.calls.append((origin,method,path,body,maximum))
        endpoint=urlsplit(path).path
        if endpoint=='/version':return b'"test-0.26"'
        if endpoint=='/speakers':return json.dumps(self.catalog).encode()
        if endpoint=='/audio_query':return json.dumps(dict(accent_phrases=[{'moras':[]}],prePhonemeLength=.1,postPhonemeLength=.1)).encode()
        if endpoint=='/synthesis':return MockVoice().synthesize('日本語の代わりの検証トーン',voice_settings({}))
        raise AssertionError(path)


def test_discover_and_single_japanese_synthesis():
    fake=EngineFake();adapter=Voicevox(send=fake)
    wav,engine=adapter.synthesize('最後の約束を、覚えている？',settings(speed=1.2,pitch=.05,intonation=1.3,volume=.8),consent=True)
    assert wav[:4]==b'RIFF' and engine['library']['speaker_uuid']==SPEAKER
    assert [urlsplit(c[2]).path for c in fake.calls]==['/version','/speakers','/audio_query','/synthesis']
    assert parse_qs(urlsplit(fake.calls[2][2]).query)['text']==['最後の約束を、覚えている？']
    body=json.loads(fake.calls[3][3]);assert body['speedScale']==1.2 and body['volumeScale']==.8
    assert body['outputSamplingRate']==16000 and body['outputStereo'] is False


@pytest.mark.parametrize('origin',['http://localhost:50021','https://127.0.0.1:50021','http://127.0.0.2:50021','http://127.1:50021',
    'http://2130706433:50021','http://192.168.0.1:50021','http://169.254.169.254','http://evil.example:50021',
    'http://user@127.0.0.1:50021','http://127.0.0.1:50021/','http://127.0.0.1:50021?url=https://evil.example','http://[::ffff:127.0.0.1]:50021'])
def test_origin_rejected_before_transport(origin):
    with pytest.raises(ValueError):Voicevox(origin,send=lambda *a:pytest.fail('must not connect'))


def test_consent_required_and_mismatched_style_rejected():
    fake=EngineFake();adapter=Voicevox(send=fake)
    with pytest.raises(ValueError):adapter.discover()
    assert fake.calls==[]
    fake.catalog[0]['speaker_uuid']=str(uuid4())
    with pytest.raises(ValueError,match='UUID'):adapter.synthesize('こんにちは',settings(),consent=True)
    assert len(fake.calls)==2


@pytest.mark.parametrize('change',['duplicate_speaker','duplicate_style','bad_uuid','bool_style','no_talk','bad_name'])
def test_speaker_validation(change):
    value=copy.deepcopy(CATALOG)
    if change=='duplicate_speaker':value*=2
    elif change=='duplicate_style':value[0]['styles']*=2
    elif change=='bad_uuid':value[0]['speaker_uuid']='wrong'
    elif change=='bool_style':value[0]['styles'][0]['id']=True
    elif change=='no_talk':value[0]['styles'][0]['type']='sing'
    else:value[0]['name']=None
    with pytest.raises(ValueError):validate_speakers(value)


@pytest.mark.parametrize('status',[301,302,307,308,400,500])
def test_transport_never_redirects_retries_or_uses_proxy(monkeypatch,status):
    import manga.voicevox as module
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin');monkeypatch.setenv('MANGA_ENABLE_LOCAL_VOICEVOX','1')
    monkeypatch.setenv('HTTP_PROXY','http://external.invalid:8000');monkeypatch.setenv('ALL_PROXY','http://external.invalid:8000')
    calls=[]
    class Connection:
        def __init__(self,host,port,timeout):calls.append((host,port,timeout))
        def request(self,*a,**kw):calls.append((a,kw))
        def getresponse(self):return type('Response',(),{'status':status})()
        def close(self):calls.append('close')
    monkeypatch.setattr(module.http.client,'HTTPConnection',Connection)
    with pytest.raises(ValueError,match='転送'):Voicevox(ORIGINS[1]).discover(consent=True)
    assert calls[0]==('::1',50021,30) and len(calls)==3


def test_default_transport_success_and_timeout_are_fake(monkeypatch):
    import manga.voicevox as module
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin');monkeypatch.setenv('MANGA_ENABLE_LOCAL_VOICEVOX','1')
    calls=[]
    class Connection:
        def __init__(self,host,port,timeout):assert host=='127.0.0.1';self.path=None
        def request(self,method,path,**kw):self.path=path;calls.append(path)
        def getresponse(self):
            content=b'"test"' if self.path=='/version' else json.dumps(CATALOG).encode()
            return type('Response',(),{'status':200,'read':lambda _,n:content})()
        def close(self):pass
    monkeypatch.setattr(module.http.client,'HTTPConnection',Connection)
    assert Voicevox().discover(consent=True)['speakers'][0]['speaker_uuid']==SPEAKER
    def timeout(*a,**kw):raise TimeoutError('private error')
    monkeypatch.setattr(Connection,'request',timeout)
    with pytest.raises(ValueError,match='接続できません') as e:Voicevox().discover(consent=True)
    assert 'private' not in str(e.value) and len(calls)==2


@pytest.mark.parametrize('system,flag',[('Linux','1'),('Darwin','0'),('Darwin','')])
def test_cloud_and_default_off_never_connect(monkeypatch,system,flag):
    import manga.voicevox as module
    monkeypatch.setattr(module.platform,'system',lambda:system);monkeypatch.setenv('MANGA_ENABLE_LOCAL_VOICEVOX',flag)
    monkeypatch.setattr(module.http.client,'HTTPConnection',lambda *a,**k:pytest.fail('must not connect'))
    assert not local_available()
    with pytest.raises(ValueError,match='Mac'):Voicevox().discover(consent=True)


@pytest.mark.parametrize('content',[b'not json',b'NaN',b'{}',b'[]',b'""',b'x'*(2*1024*1024+1)])
def test_bad_engine_response(content):
    with pytest.raises(ValueError):Voicevox(send=lambda *a:content).discover(consent=True)


@pytest.mark.parametrize('fields',[{'speed':float('nan')},{'pitch':1},{'intonation':-1},{'volume':5},{'emotion':'angry'}])
def test_bad_voice_parameters(fields):
    with pytest.raises(ValueError):settings(**fields)
