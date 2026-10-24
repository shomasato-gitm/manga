"""Exercise the real HTTP parsing boundary with an in-memory opener, never the network."""
import io
import json
from types import SimpleNamespace

import pytest

from manga import voice_polly,voice_audio
from manga.voice_models import voice_settings


class Response(io.BytesIO):
    def __init__(self,content,headers=None):
        super().__init__(content)
        self.headers=headers or {'Content-Type':'audio/pcm','x-amzn-RequestCharacters':'2'}


def adapter_with(monkeypatch,response):
    calls=[]
    class Opener:
        def open(self,request,timeout):calls.append((request,timeout));return response
    monkeypatch.setattr(voice_polly,'build_opener',lambda *args:Opener())
    return voice_polly.PollyVoice(keys=dict(AWS_ACCESS_KEY_ID='fake',AWS_SECRET_ACCESS_KEY='dummy-key')),calls


def test_http_payload_and_case_insensitive_usage_header(monkeypatch):
    adapter,calls=adapter_with(monkeypatch,Response(b'\0\0'*100))
    pcm,count=adapter.synthesize('<speak>確認</speak>',dict(engine='neural',voice_id='Takumi'))
    assert count==2 and len(pcm)==200 and len(calls)==1
    request,timeout=calls[0]
    body=json.loads(request.data)
    assert timeout==20 and body['LanguageCode']=='ja-JP' and body['OutputFormat']=='pcm'
    assert body['SampleRate']=='16000' and body['Engine']=='neural'


@pytest.mark.parametrize('kind',['content_type','too_large','deadline','socket_timeout'])
def test_bad_http_response_fails_closed_without_secret_output(monkeypatch,kind):
    response=Response(b'\0\0'*100)
    if kind=='content_type':response.headers['Content-Type']='application/json'
    if kind=='too_large':monkeypatch.setattr(voice_polly,'MAX_WAV_BYTES',10)
    if kind=='deadline':
        clock=iter([0,0,100])
        monkeypatch.setattr(voice_polly.time,'monotonic',lambda:next(clock))
    if kind=='socket_timeout':
        def fail(*args):raise TimeoutError('dummy-sensitive-key')
        response.read1=fail
    adapter,calls=adapter_with(monkeypatch,response)
    with pytest.raises(voice_polly.VoiceProviderError) as error:
        adapter.synthesize('<speak>確認</speak>',dict(engine='neural',voice_id='Takumi'))
    assert 'dummy-sensitive-key' not in str(error.value) and len(calls)==1


def test_redirect_is_never_followed():
    with pytest.raises(voice_polly.VoiceProviderError):voice_polly.NoRedirect().redirect_request(None,None,None,None,None,None)


def test_wav_truncation_and_no_ffprobe_fallback(monkeypatch):
    content=voice_audio.MockVoice().synthesize('確認',voice_settings({}))
    with pytest.raises(ValueError):voice_audio.inspect_wav(content[:-20])
    monkeypatch.setattr(voice_audio.shutil,'which',lambda _:None)
    info=voice_audio.inspect_wav(content)
    assert voice_audio.verify_with_ffprobe(None,info)['measurement']=='wave'


def test_ffprobe_uses_argument_array_and_rejects_wrong_measurement(monkeypatch,tmp_path):
    content=voice_audio.MockVoice().synthesize('確認',voice_settings({}))
    path=tmp_path/'audio.wav';path.write_bytes(content);info=voice_audio.inspect_wav(content)
    monkeypatch.setattr(voice_audio.shutil,'which',lambda _: '/fake/ffprobe')
    seen=[]
    def run(args,**kwargs):
        seen.append((args,kwargs))
        return SimpleNamespace(stdout=json.dumps(dict(streams=[dict(codec_name='pcm_s16le',sample_rate='16000',channels=1)],format=dict(duration='999'))).encode())
    monkeypatch.setattr(voice_audio.subprocess,'run',run)
    with pytest.raises(ValueError):voice_audio.verify_with_ffprobe(path,info)
    assert isinstance(seen[0][0],list) and seen[0][1]['timeout']==10 and 'shell' not in seen[0][1]
