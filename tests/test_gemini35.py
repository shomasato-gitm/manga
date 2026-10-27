"""3.5のモデル・費用・単発送信・旧履歴をフェイクだけで検証する。"""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from manga.budget.config import open_budget
from manga.gemini_models import DEFAULT_MODEL,LEGACY_MODEL,MODELS,get_model
from manga.gemini_diagnostics import Probe,preview,run,recover,diagnostic_history,probe_body
from manga.story_ai_gemini import template,FLAGS,quote,GeminiStoryAI,parse_response
from manga.story_ai_mock import MockStoryAI
from manga.story_ai_requests import next_request
from manga.story_ai_pricing import charge
from story_ai_fixtures import ai_workspace,concepts,job
from test_story_ai_gemini import next_step
from test_gemini_diagnostics import prepare as old_prepare,execute as old_execute
from text_guard_fakes import guarded

SECRET='PRIVATE-GEMINI35-KEY'


def card35(*,access=True,free=False):
    value=template(DEFAULT_MODEL)
    today=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat()
    value.update(verified_on=today,fx_jpy_per_usd='150',tax_multiplier='1.10',tax_note='TEST ONLY hypothetical FX/tax',
                 free_tier_reviewed=free,free_tier_verified_on=today if free else '',**{flag:True for flag in FLAGS})
    value['account_access_reviewed']=access
    return value


def raw35(text='OK',*,model=DEFAULT_MODEL):
    return dict(modelVersion=model,usageMetadata=dict(promptTokenCount=50,candidatesTokenCount=100,thoughtsTokenCount=13,totalTokenCount=163),
                candidates=[dict(finishReason='STOP',content=dict(parts=[dict(text=text)]))])


def setup35(service,monkeypatch,*,mutate=None):
    service.configure(service.store.load()['settings'],card35(free=True),expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1');monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1')
    calls=[]
    def send(body,key):
        calls.append(body)
        request=service.store.load()['requests'][-1]
        raw=raw35(MockStoryAI().generate(request)['text'],model=request['card']['model'])
        return mutate(raw) if mutate else raw
    service.adapter=GeminiStoryAI(send=send,key=SECRET)
    return calls


def probe35(service,monkeypatch,*,raw=None,mode='verified-free'):
    import manga.story_ai_gemini as module
    monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    value=card35(access=False,free=True);plan=preview(service,value,mode);calls=[]
    class Reply(io.BytesIO):
        status=200
        def read1(self,n):return self.read(n)
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            return Reply(json.dumps(raw or raw35()).encode())
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    result=run(service,value,mode=mode,approval=plan['approval_key'],expected_revision=plan['revision'])
    return result,calls,value


def test_registry_and_templates_do_not_upgrade_legacy_configuration():
    assert DEFAULT_MODEL=='gemini-3.5-flash-lite' and LEGACY_MODEL=='gemini-2.5-flash-lite'
    assert template()['model']==LEGACY_MODEL and template()['thinking_budget']==0
    newer=template(DEFAULT_MODEL)
    assert newer['thinking_level']=='minimal' and 'thinking_budget' not in newer
    assert newer['input_per_million_usd']=='0.30' and newer['output_per_million_usd']=='2.50'
    assert newer['max_input_tokens']==1048576 and get_model(DEFAULT_MODEL).output_limit==65536
    assert newer['verified_on']=='' and not newer['account_access_reviewed'] and not newer['free_tier_reviewed']
    path=Path(__file__).resolve().parents[1]/'examples/story_ai/gemini_35_text_pricing_template.json'
    assert json.loads(path.read_text())==newer
    with pytest.raises(TypeError):MODELS['unverified']=get_model(DEFAULT_MODEL)
    with pytest.raises(ValueError):get_model('gemini-unverified')


@pytest.mark.parametrize('change',[
    {'input_per_million_usd':'0.10'},{'output_per_million_usd':'0.40'},
    {'input_per_million_usd':None},{'output_per_million_usd':'0'},
    {'max_input_tokens':10000},{'max_output_tokens':65537},
    {'thinking_level':'high'},{'thinking_budget':0},{'thinking_level':None},
    {'returned_model_ids':[LEGACY_MODEL]},{'model_url':get_model(LEGACY_MODEL).model_url},
    {'model':'gemini-unverified'},{'maximum_billing_reviewed':False},{'verified_on':''},
])
def test_wrong_model_evidence_fails_before_saving_or_sending(ai_workspace,change):
    service=ai_workspace;value=card35();value.update(change);before=service.store.path.read_bytes()
    with pytest.raises(ValueError):service.configure(service.store.load()['settings'],value,expected_revision=service.store.load()['revision'])
    assert service.store.path.read_bytes()==before and not service.store.load()['jobs']


def test_thought_tokens_are_inside_positive_maximum_and_actual_equivalent(ai_workspace):
    from manga.story_ai_pricing import limits
    value=card35();price=quote(value);bounds=limits(value)
    assert price.rates.model==DEFAULT_MODEL and bounds['max_output_tokens']==16384
    assert price.execution_evidence('gemini-text',DEFAULT_MODEL,bounds)['computed_max_jpy']=='73.328640'
    assert preview(ai_workspace,value)['maximum_jpy']=='65.012640'
    result=parse_response(raw35(),model=DEFAULT_MODEL)
    assert result['usage']=={'input_tokens':50,'output_tokens':113}
    assert str(charge(value,result))=='0.049088'
    assert parse_response(raw35(),model=LEGACY_MODEL)['model'] is None


def test_real_http_shape_of_free_probe_is_model_bound_and_safe(ai_workspace,monkeypatch,capsys):
    service=ai_workspace;original=service.stories.path.read_bytes();result,calls,value=probe35(service,monkeypatch)
    assert result['state']=='success' and result['model']==DEFAULT_MODEL and len(calls)==1
    request=calls[0];body=json.loads(request.data)
    assert request.full_url=='https://generativelanguage.googleapis.com/v1beta/models/'+DEFAULT_MODEL+':generateContent'
    assert request.get_header('X-goog-api-key')==SECRET and SECRET.encode() not in request.data
    assert body==probe_body(DEFAULT_MODEL) and body['contents'][0]['parts'][0]['text'].endswith('exactly OK.')
    assert body['generationConfig']['thinkingConfig']=={'thinkingLevel':'minimal'}
    assert 'responseJsonSchema' not in body['generationConfig']
    book=open_budget(service.directory);status=book.request_status(result['diagnosis_id'])
    assert status['model']==DEFAULT_MODEL and status['state']=='settled' and status['actual_jpy']>0
    assert recover(service,result['diagnosis_id'])['state']=='success' and len(calls)==1
    assert service.stories.path.read_bytes()==original and not value['account_access_reviewed'] and service.store.load()['card'] is None
    for path in service.store.root.rglob('*'):
        if path.is_file():assert SECRET.encode() not in path.read_bytes()
    output=capsys.readouterr();assert SECRET not in output.out+output.err


@pytest.mark.parametrize('which',['wrong_model','missing_usage','wrong_reply'])
def test_http200_does_not_make_unverified_results_success(ai_workspace,monkeypatch,which):
    raw=raw35()
    if which=='wrong_model':raw['modelVersion']=LEGACY_MODEL
    elif which=='missing_usage':raw['usageMetadata']={}
    else:raw['candidates'][0]['content']['parts'][0]['text']='{"ok":true}'
    result,calls,_=probe35(ai_workspace,monkeypatch,raw=raw)
    assert result['state']!='success' and len(calls)==1 and not ai_workspace.store.load()['adoptions']
    if which!='wrong_reply':assert open_budget(ai_workspace.directory).request_status(result['diagnosis_id'])['state']=='uncertain'


def test_free_tier_key_and_individual_approval_are_not_inferred_from_mac_report(ai_workspace,monkeypatch):
    service=ai_workspace;value=card35(access=False);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    with pytest.raises(ValueError,match='無料枠'):preview(service,value,'verified-free')
    value=card35(access=False,free=True);plan=preview(service,value,'verified-free')
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    with pytest.raises(ValueError):run(service,value,mode='verified-free',approval=plan['approval_key'],expected_revision=plan['revision'])
    monkeypatch.setenv('GEMINI_API_KEY',SECRET)
    with pytest.raises(ValueError,match='承認'):run(service,value,mode='verified-free',expected_revision=plan['revision'])
    with pytest.raises(ValueError):quote(value)
    assert not diagnostic_history(service)


def test_old_model_approval_does_not_authorize_new_model(ai_workspace,monkeypatch):
    from test_gemini_diagnostics import draft
    service=ai_workspace;old_prepare(service,monkeypatch);plan=preview(service,draft());value=card35(access=False)
    with pytest.raises(ValueError,match='承認'):run(service,value,approval=plan['approval_key'],expected_revision=plan['revision'])
    assert not diagnostic_history(service)


@pytest.mark.parametrize('scope',['project','monthly','legacy_uncertain'])
def test_new_model_cannot_bypass_shared_budget_or_old_uncertain_bill(ai_workspace,monkeypatch,scope):
    service=ai_workspace;setup35(service,monkeypatch);book=open_budget(service.directory)
    for _ in range(10 if scope=='monthly' else 1):
        identifier=str(uuid4())
        book.reserve(project_id=str(uuid4()) if scope=='monthly' else service.project_id,request_id=identifier,
            provider='gemini-text',model=LEGACY_MODEL,kind='legacy',max_jpy='500' if scope!='legacy_uncertain' else '23.8128')
        if scope=='legacy_uncertain':book.mark_uncertain(identifier)
    with pytest.raises(ValueError):job(service,'concepts','gemini-text')
    assert not service.store.load()['jobs']


def test_concepts_8_page_story_evaluation_and_improvement_use_new_model(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup35(service,monkeypatch);before=service.stories.path.read_bytes()
    concepts(service,'gemini-text');created=job(service,'story','gemini-text');service.run_job(created['id'])
    assert len(calls)==2
    next_step(service,created['id']);next_step(service,created['id']);assert len(calls)==4
    for body in calls:
        config=body['generationConfig']
        assert config['thinkingConfig']=={'thinkingLevel':'minimal'} and 'thinkingBudget' not in config['thinkingConfig']
        assert config['responseFormat']['text']['mimeType']=='APPLICATION_JSON'
        assert config['responseFormat']['text']['schema']['additionalProperties'] is False
        assert 'responseJsonSchema' not in config and 'responseMimeType' not in config
    data=service.store.load();candidate=data['candidates'][0]
    assert len(candidate['story']['pages'])==8 and sum(len(p['panels']) for p in candidate['story']['pages'])==24
    assert all(j['card']['model']==DEFAULT_MODEL for j in data['jobs'])
    assert service.stories.path.read_bytes()==before and not data['adoptions']


@pytest.mark.parametrize('fault',['json','page_count','model','usage'])
def test_invalid_new_model_generation_is_not_adopted_or_retried(ai_workspace,monkeypatch,fault):
    service=ai_workspace;calls=setup35(service,monkeypatch);concepts(service,'gemini-text')
    def mutate(raw):
        if fault=='model':raw['modelVersion']=LEGACY_MODEL
        elif fault=='usage':raw['usageMetadata']={}
        elif fault=='json':raw['candidates'][0]['content']['parts'][0]['text']='invalid JSON'
        else:
            text=json.loads(raw['candidates'][0]['content']['parts'][0]['text']);text['story']['pages'].pop()
            raw['candidates'][0]['content']['parts'][0]['text']=json.dumps(text)
        return raw
    calls=setup35(service,monkeypatch,mutate=mutate);created=job(service,'story','gemini-text');result=service.run_job(created['id'])
    assert len(calls)==1 and result['stop_reason'] in ('invalid_output','provider_uncertain')
    assert not service.store.load()['candidates'] and not service.store.load()['adoptions']
    with pytest.raises(ValueError):next_step(service,created['id'])


def test_new_model_request_cannot_use_old_model_execution_right(ai_workspace,monkeypatch,tmp_path):
    from test_story_ai_gemini import card as old_card
    service=ai_workspace;calls=setup35(service,monkeypatch);created=job(service,'concepts','gemini-text');request=next_request(service,created['id'])
    with pytest.raises(ValueError,match='実行権'):
        guarded(tmp_path,lambda:service.adapter.generate(request),provider='gemini-text',request_id=request['id'],card=old_card())
    assert not calls


def test_mixed_diagnostic_history_recovers_old_missing_model_without_migration(ai_workspace,monkeypatch):
    service=ai_workspace;save=service.store.save
    def legacy_save(data,kind,**fields):
        if kind=='gemini_connection_diagnostic':fields.pop('model',None)
        return save(data,kind,**fields)
    monkeypatch.setattr(service.store,'save',legacy_save)
    old_probe,old_calls=old_prepare(service,monkeypatch);older=old_execute(service,old_probe)
    monkeypatch.setattr(service.store,'save',save)
    assert 'model' not in older
    book=open_budget(service.directory);old_status=book.request_status(older['diagnosis_id'])
    newer,calls,_=probe35(service,monkeypatch)
    assert newer['model']==DEFAULT_MODEL and recover(service,older['diagnosis_id'])['state']=='success'
    assert book.request_status(older['diagnosis_id'])==old_status and len(old_calls)==len(calls)==1
    assert len(diagnostic_history(service))==2


def test_concurrent_new_model_probe_uses_one_approval_once(ai_workspace,monkeypatch):
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    value=card35(access=False,free=True);plan=preview(service,value,'verified-free');calls=[]
    def send(*a):calls.append(1);return raw35()
    def work(_):
        try:return run(service,value,mode='verified-free',approval=plan['approval_key'],expected_revision=plan['revision'],probe=Probe(send=send))
        except Exception:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,range(2)))
    assert calls==[1] and sum(r is not None for r in results)==1


def test_normal_http_transport_uses_new_model_format_and_counts_thoughts(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as module
    service=ai_workspace;setup35(service,monkeypatch);service.adapter=None;calls=[]
    class Reply(io.BytesIO):
        status=200
        def read1(self,n):return self.read(n)
    class Opener:
        def open(self,request,timeout):
            calls.append(request);source=service.store.load()['requests'][-1]
            return Reply(json.dumps(raw35(MockStoryAI().generate(source)['text'])).encode())
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    created=job(service,'concepts','gemini-text');service.run_job(created['id'])
    assert len(calls)==1 and calls[0].full_url.endswith('/'+DEFAULT_MODEL+':generateContent')
    config=json.loads(calls[0].data)['generationConfig']
    assert config['responseFormat']['text']['mimeType']=='APPLICATION_JSON'
    assert config['thinkingConfig']=={'thinkingLevel':'minimal'}
    stored=service.store.load();assert stored['concept_batches'] and stored['jobs'][0]['card']['model']==DEFAULT_MODEL
    status=open_budget(service.directory).request_status(stored['requests'][0]['id'])
    assert str(status['actual_jpy'])=='0.049088'


def test_stopped_legacy_job_keeps_its_model_and_quote_after_new_setting_saved(ai_workspace,monkeypatch):
    from test_story_ai_gemini import setup as old_setup
    service=ai_workspace;calls=old_setup(service,monkeypatch);concepts(service,'gemini-text')
    created=job(service,'story','gemini-text');service.run_job(created['id'])
    service.configure(service.store.load()['settings'],card35(),expected_revision=service.store.load()['revision'])
    next_step(service,created['id'])
    data=service.store.load();old_job=next(j for j in data['jobs'] if j['id']==created['id'])
    assert data['card']['model']==DEFAULT_MODEL and old_job['card']['model']==LEGACY_MODEL
    assert all(body['generationConfig']['thinkingConfig']=={'thinkingBudget':0} for body in calls)
    assert all(r['card']['model']==LEGACY_MODEL for r in data['requests'])


@pytest.mark.parametrize('code',[404,429])
def test_free_model_http_failure_does_not_switch_model_or_paid_mode(ai_workspace,monkeypatch,code):
    import manga.story_ai_gemini as module
    from urllib.error import HTTPError
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    monkeypatch.delenv('MANGA_ENABLE_PAID_STORY',raising=False);calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request);raise HTTPError(request.full_url,code,SECRET,{},io.BytesIO(SECRET.encode()))
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    value=card35(access=False,free=True);plan=preview(service,value,'verified-free')
    result=run(service,value,mode='verified-free',approval=plan['approval_key'],expected_revision=plan['revision'])
    assert result['model']==DEFAULT_MODEL and result['state']=='unavailable' and len(calls)==1
    assert open_budget(service.directory).request_status(result['diagnosis_id'])['state']=='uncertain'
    assert not service.paid_enabled()
    with pytest.raises(ValueError):run(service,value,mode='verified-free',approval=plan['approval_key'],expected_revision=service.store.load()['revision'])
    assert len(calls)==1


@pytest.mark.parametrize('field,expected',[
    ('generationConfig.responseFormat.text.schema','bad_request_json_schema'),
    ('generation_config.response_format.text.mime_type','bad_request_mime_type'),
])
def test_new_format_errors_are_classified_without_raw_fields(field,expected):
    from manga.gemini_http_errors import classify_bad_request
    from test_gemini_http400 import error_body
    assert classify_bad_request(error_body(fields=[field]))==expected


@pytest.mark.parametrize('tier',['priority','flex','unverified-tier'])
def test_unexpected_service_tier_does_not_use_standard_rates(tier):
    raw=raw35();raw['usageMetadata']['serviceTier']=tier
    parsed=parse_response(raw,model=DEFAULT_MODEL)
    assert parsed['usage'] is None and parsed['model']==DEFAULT_MODEL


@pytest.mark.parametrize('extra',[{'tools':[{'googleSearch':{}}]},{'serviceTier':'priority'}])
def test_transport_rejects_unquoted_features_before_http(ai_workspace,monkeypatch,tmp_path,extra):
    import manga.story_ai_gemini as module
    from manga.budget.guard import claim_provider_call
    from manga.voice_models import digest
    service=ai_workspace;setup35(service,monkeypatch);created=job(service,'concepts','gemini-text')
    request=next_request(service,created['id']);body=module.payload(request);body.update(extra)
    monkeypatch.setattr(module,'build_opener',lambda *a:pytest.fail('unquoted feature reached HTTP'))
    def operation():
        claim_provider_call('gemini-text',DEFAULT_MODEL,payload_sha256=digest(body))
        return module.transport(body,SECRET)
    with pytest.raises(ValueError,match='料金tier'):
        guarded(tmp_path,operation,provider='gemini-text',request_id=request['id'],card=card35())
