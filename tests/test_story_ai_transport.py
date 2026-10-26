import io
import json

import pytest

from manga.story_ai_provider import transport,NoRedirect,StoryProviderError,ResponsesStoryAI
from story_ai_fixtures import ai_workspace,job,paid


class Response:
    def __init__(self,data):self.data=io.BytesIO(data)
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def read1(self,size):return self.data.read(size)


def test_transport_uses_fixed_https_no_redirect_no_automatic_retry(monkeypatch,tmp_path):
    import manga.story_ai_provider as provider
    captured=[]
    class Opener:
        def open(self,request,timeout):
            captured.append((request,timeout));return Response(b'{"status":"completed"}')
    monkeypatch.setattr(provider,'build_opener',lambda handler:Opener())
    assert send_guarded(tmp_path,{'model':'test-story-model','input':'日本語'})['status']=='completed'
    request,timeout=captured[0]
    assert request.full_url=='https://api.openai.com/v1/responses' and request.get_method()=='POST' and timeout==30
    assert b'test-secret' not in request.data and request.headers['Authorization']=='Bearer test-secret'
    with pytest.raises(StoryProviderError):NoRedirect().redirect_request(None,None,None,None,None,None)


@pytest.mark.parametrize('mode',['timeout','oversize','invalid_json','deadline'])
def test_transport_errors_are_sanitized_and_bounded(monkeypatch,mode,tmp_path):
    import manga.story_ai_provider as provider
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(1)
            if mode=='timeout':raise TimeoutError('Authorization Bearer secret')
            return Response(b'x'*1048577 if mode=='oversize' else b'{bad' if mode=='invalid_json' else b'{}')
    monkeypatch.setattr(provider,'build_opener',lambda handler:Opener())
    if mode=='deadline':
        values=iter([0,121]);monkeypatch.setattr(provider.time,'monotonic',lambda:next(values))
    with pytest.raises(StoryProviderError) as error:send_guarded(tmp_path,{'model':'test-story-model'})
    assert 'secret' not in str(error.value) and len(calls)==1


def test_refusal_and_incomplete_are_saved_charged_and_not_retried(ai_workspace,monkeypatch):
    def refuse(data,request):data['output']=[dict(type='message',content=[dict(type='refusal',refusal='No')])];return data
    service=ai_workspace;calls=paid(service,monkeypatch,mutate=refuse);created=job(service,'concepts','openai-responses');result=service.run_job(created['id'])
    assert result['stop_reason']=='invalid_output' and len(calls)==1 and not service.store.load()['concept_batches']


def test_size_check_happens_before_reservation(ai_workspace,monkeypatch):
    import manga.story_ai_provider as provider
    from manga.budget.config import open_budget
    service=ai_workspace;calls=paid(service,monkeypatch)
    monkeypatch.setattr(provider,'prompt',lambda *a:'x'*262145)
    created=job(service,'concepts','openai-responses');result=service.run_job(created['id'])
    assert result['stop_reason']=='input_invalid' and not calls
    request=service.store.load()['requests'][0];assert open_budget(service.directory).request_status(request['id']) is None


def send_guarded(tmp_path,body):
    from text_guard_fakes import guarded
    from manga.budget.guard import claim_provider_call
    from manga.voice_models import digest
    def operation():
        claim_provider_call('openai-responses','test-story-model',payload_sha256=digest(body))
        return transport(body,'test-secret')
    return guarded(tmp_path,operation)
