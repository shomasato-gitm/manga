"""フェイクのみ。無料枠も実際には消費しない。"""
import copy
import json
import io
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

import pytest

from manga.budget.config import open_budget
from manga.story_ai_gemini import GeminiStoryAI,template,FLAGS,MODEL,transport
from manga.story_ai_mock import MockStoryAI
from manga.story_ai_pricing import evidence
from manga.voice_models import digest
from story_ai_fixtures import ai_workspace,job,concepts
from text_guard_fakes import guarded


def card():
    value=template();value.update(verified_on=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat(),fx_jpy_per_usd='150',tax_multiplier='1.10',tax_note='TEST ONLY hypothetical FX/tax',**{k:True for k in FLAGS})
    return value


def setup(service,monkeypatch,*,mutate=None):
    service.configure(service.store.load()['settings'],card(),expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1');calls=[]
    def send(body,key):
        calls.append(body);request=service.store.load()['requests'][-1];response=MockStoryAI().generate(request)
        raw=dict(modelVersion=MODEL,usageMetadata=dict(promptTokenCount=50,candidatesTokenCount=100,totalTokenCount=150),
            candidates=[dict(finishReason='STOP',content=dict(parts=[dict(text=response['text'])]))])
        return mutate(raw) if mutate else raw
    service.adapter=GeminiStoryAI(send=send,key='fake-key-no-network')
    return calls


def next_step(service,identifier):
    value=service.next_estimate(identifier)
    service.approve_next(identifier,approval=value['approval_key'],expected_revision=service.store.load()['revision'])
    return service.run_job(identifier)


def test_concepts_draft_evaluate_improve_are_separately_approved(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup(service,monkeypatch);original=service.stories.path.read_bytes()
    concepts(service,'gemini-text');j=job(service,'story','gemini-text');result=service.run_job(j['id'])
    assert len(calls)==2 and result['phase']=='evaluate' and result['stop_reason']=='approval_required'
    with pytest.raises(ValueError):service.resume(j['id'],confirmed=True)
    assert next_step(service,j['id'])['phase']=='improve' and len(calls)==3
    assert next_step(service,j['id'])['phase']=='reevaluate' and len(calls)==4
    assert service.stories.path.read_bytes()==original and not service.store.load()['adoptions']
    assert len(service.store.load()['candidates'])==2
    assert all('tools' not in body and body['generationConfig']['thinkingConfig']=={'thinkingBudget':0} for body in calls)
    c=service.store.load()['candidates'][0];assert len(c['story']['pages'])==8 and sum(len(p['panels']) for p in c['story']['pages'])==24
    assert any('人物' in w for w in c['warnings'])


@pytest.mark.parametrize('change',[{'model':'other'},{'max_input_tokens':1000},{'thinking_budget':1},{'input_per_million_usd':'0'},
    {'availability_reviewed':False},{'account_access_reviewed':False},{'external_data_reviewed':False},{'api_key':'must-not-save'}])
def test_missing_or_wrong_evidence_stops_without_write(ai_workspace,change):
    service=ai_workspace;value=card();value.update(change);before=service.store.path.read_bytes()
    with pytest.raises(ValueError):service.configure(service.store.load()['settings'],value,expected_revision=service.store.load()['revision'])
    assert service.store.path.read_bytes()==before


def test_missing_key_checked_before_job_or_reservation(ai_workspace,monkeypatch):
    service=ai_workspace;service.configure(service.store.load()['settings'],card(),expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1');monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    with pytest.raises(ValueError,match='GEMINI_API_KEY'):job(service,'concepts','gemini-text')
    assert not service.store.load()['jobs']


def test_verified_free_needs_account_confirmation_and_positive_budget(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup(service,monkeypatch)
    with pytest.raises(ValueError,match='無料枠'):service.estimate('concepts',provider='gemini-text',mode='verified-free')
    value=card();value.update(free_tier_reviewed=True,free_tier_verified_on=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat())
    service.configure(service.store.load()['settings'],value,expected_revision=service.store.load()['revision'])
    monkeypatch.delenv('MANGA_ENABLE_PAID_STORY');monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    estimate=service.estimate('concepts',provider='gemini-text',mode='verified-free');assert float(estimate['per_call_max_jpy'])>0
    created=service.create_job('concepts',provider='gemini-text',mode='verified-free',approval=estimate['approval_key'],expected_revision=service.store.load()['revision'])
    service.run_job(created['id'])
    assert len(calls)==1 and open_budget(service.directory).summary(service.project_id)['project_used_jpy']>0
    assert created['access_mode']=='verified-free'


@pytest.mark.parametrize('mode',['model','usage','timeout','json','refusal','multiple','tool'])
def test_unknown_or_bad_results_never_adopt_or_retry(ai_workspace,monkeypatch,mode):
    def mutate(raw):
        if mode=='model':raw['modelVersion']='expensive-other'
        elif mode=='usage':raw['usageMetadata'].pop('totalTokenCount')
        elif mode=='timeout':raise TimeoutError('secret-sensitive-content')
        elif mode=='json':raw['candidates'][0]['content']['parts'][0]['text']='{broken'
        elif mode=='refusal':raw['candidates'][0]['finishReason']='SAFETY'
        elif mode=='multiple':raw['candidates']*=2
        else:raw['candidates'][0]['content']['parts']=[dict(functionCall=dict(name='forbidden'))]
        return raw
    service=ai_workspace;calls=setup(service,monkeypatch,mutate=mutate);created=job(service,'concepts','gemini-text')
    result=service.run_job(created['id']);assert result['stop_reason'] in ('provider_uncertain','invalid_output')
    assert not service.store.load()['concept_batches'] and not service.store.load()['adoptions'] and len(calls)==1
    assert 'secret-sensitive' not in service.store.path.read_text()
    with pytest.raises(ValueError):next_step(service,created['id'])
    request=service.store.load()['requests'][0]
    assert open_budget(service.directory).request_status(request['id'])['state']==('uncertain' if mode in ('model','usage','timeout') else 'settled')


def test_old_approval_rejected_after_new_request_and_response_save_recovers(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup(service,monkeypatch)
    estimate=service.estimate('concepts',provider='gemini-text',new_request=True)
    created=service.create_job('concepts',provider='gemini-text',new_request=True,approval=estimate['approval_key'],expected_revision=service.store.load()['revision'])
    original=service.store.save
    def fail(data,kind,**kw):
        if kind=='response_applied':raise OSError('disk')
        return original(data,kind,**kw)
    monkeypatch.setattr(service.store,'save',fail);service.run_job(created['id']);monkeypatch.setattr(service.store,'save',original)
    service.recover(created['id']);assert len(calls)==1 and len(service.store.load()['concept_batches'])==1
    with pytest.raises(ValueError,match='明示承認'):service.create_job('concepts',provider='gemini-text',new_request=True,approval=estimate['approval_key'],expected_revision=service.store.load()['revision'])


def test_no_adapter_or_raw_transport_can_bypass_claim(ai_workspace,monkeypatch,tmp_path):
    service=ai_workspace;calls=setup(service,monkeypatch);created=job(service,'concepts','gemini-text')
    from manga.story_ai_requests import next_request
    request=next_request(service,created['id'])
    with pytest.raises(ValueError):service.adapter.generate(request)
    with pytest.raises(ValueError):transport({},'dummy')
    assert not calls
    def operation():
        result=service.adapter.generate(request)
        with pytest.raises(ValueError):service.adapter.generate(request)
        return result
    guarded(tmp_path,operation,provider='gemini-text',request_id=request['id'],card=card());assert len(calls)==1


def test_actual_http_shape_uses_fixed_host_header_no_secret_in_body(ai_workspace,monkeypatch,tmp_path):
    import manga.story_ai_gemini as module
    service=ai_workspace;setup(service,monkeypatch);service.adapter=None
    monkeypatch.setenv('GEMINI_API_KEY','TEST-header-secret')
    captured=[]
    class Response(io.BytesIO):
        def read1(self,n):return self.read(n)
    class Opener:
        def open(self,request,timeout):
            captured.append(request)
            return Response(json.dumps(dict(modelVersion=MODEL,usageMetadata=dict(promptTokenCount=1,candidatesTokenCount=1,totalTokenCount=2),
                candidates=[dict(finishReason='STOP',content=dict(parts=[dict(text=MockStoryAI().generate(service.store.load()['requests'][-1])['text'])]))])).encode())
    monkeypatch.setattr(module,'build_opener',lambda _:Opener())
    created=job(service,'concepts','gemini-text');service.run_job(created['id'])
    req=captured[0];assert req.full_url=='https://generativelanguage.googleapis.com/v1beta/models/'+MODEL+':generateContent'
    assert req.get_header('X-goog-api-key')=='TEST-header-secret' and b'TEST-header-secret' not in req.data
    assert 'TEST-header-secret' not in service.store.path.read_text()


@pytest.mark.parametrize('change',['missing_dialogue','bad_page','bad_panel','page_count'])
def test_story_structure_errors_are_candidates_never_adopted(ai_workspace,monkeypatch,change):
    service=ai_workspace;setup(service,monkeypatch);concepts(service,'gemini-text')
    def mutate(raw):
        value=json.loads(raw['candidates'][0]['content']['parts'][0]['text'])
        if change=='missing_dialogue':value['story']['pages'][0]['panels'][0].pop('dialogue')
        elif change=='bad_page':value['story']['pages'][0]['page']=2
        elif change=='bad_panel':value['story']['pages'][0]['panels'][0]['panel']=2
        else:value['story']['pages'].pop()
        raw['candidates'][0]['content']['parts'][0]['text']=json.dumps(value);return raw
    setup(service,monkeypatch,mutate=mutate)
    # configure保存は条件トークンを変更しない。同じ採用企画を使用できる。
    created=job(service,'story','gemini-text');result=service.run_job(created['id'])
    assert result['stop_reason']=='invalid_output' and not service.store.load()['candidates']


def test_unknown_character_is_flagged_for_human_comparison(ai_workspace,monkeypatch):
    service=ai_workspace;setup(service,monkeypatch);concepts(service,'gemini-text')
    def mutate(raw):
        value=json.loads(raw['candidates'][0]['content']['parts'][0]['text'])
        value['story']['pages'][0]['panels'][0]['dialogue']='未登録人物：「確認してください。」'
        raw['candidates'][0]['content']['parts'][0]['text']=json.dumps(value);return raw
    setup(service,monkeypatch,mutate=mutate);created=job(service,'story','gemini-text');service.run_job(created['id'])
    assert any('未登録の話者' in w for w in service.store.load()['candidates'][0]['warnings'])
