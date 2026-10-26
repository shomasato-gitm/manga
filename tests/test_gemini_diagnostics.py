"""HTTPはフェイク。診断でも本物のAPI・無料枠を使わない。"""
import io
import json
import copy
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from manga.budget.config import open_budget
from manga.storage import StorageError
from manga.story_ai_gemini import quote,GeminiStoryAI,MODEL,GeminiTransportError
from manga.gemini_diagnostics import offline_report,preview,run,recover,Probe,probe_body,diagnostic_history,PROVIDER,LIMITS
from story_ai_fixtures import ai_workspace,job
from test_story_ai_gemini import card


def draft():
    value=card();value['account_access_reviewed']=False;return value


def response():
    return dict(modelVersion=MODEL,usageMetadata=dict(promptTokenCount=12,candidatesTokenCount=4,totalTokenCount=16),
        candidates=[dict(finishReason='STOP',content=dict(parts=[dict(text='{"ok":true}')]))])


def prepare(service,monkeypatch,*,send=None):
    monkeypatch.setenv('GEMINI_API_KEY','TEST-PRIVATE-KEY');monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1')
    calls=[]
    def fake(body,key):
        calls.append(body);return send(body,key) if send else response()
    return Probe(send=fake),calls


def execute(service,probe,*,raw=None,mode='bounded'):
    value=raw or draft();plan=preview(service,value,mode)
    return run(service,value,probe=probe,mode=mode,approval=plan['approval_key'],expected_revision=service.store.load()['revision'])


@pytest.mark.parametrize('raw',[None,'','{broken','[]',json.dumps({'api_key':'TEST-PRIVATE-KEY'}),json.dumps({'model':'not-a-model'}),json.dumps(draft())])
def test_offline_works_without_confirmed_card_and_never_writes(ai_workspace,monkeypatch,raw):
    import manga.budget.config as module
    service=ai_workspace;before=service.store.path.read_bytes()
    monkeypatch.setenv('GEMINI_API_KEY','TEST-PRIVATE-KEY')
    monkeypatch.setattr(module,'open_budget',lambda *a:pytest.fail('offline must not open ledger'))
    result=offline_report(raw)
    assert result['key_set'] and result['key_format'] and result['network_calls']==0 and result['budget_reservations']==0
    assert 'TEST-PRIVATE-KEY' not in json.dumps(result)
    assert service.store.path.read_bytes()==before and not service.store.load()['card']
    assert not list(service.store.root.glob('story_ai/diagnostics/*'))


def test_offline_separates_self_reported_confirmation_and_actual_permissions(monkeypatch):
    from manga.story_ai_gemini import template
    raw=template();monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    report=offline_report(raw)
    assert not report['key_format'] and report['account_access']=='未確認' and report['free_tier']=='未確認'
    assert '同梱' in report['model_information'] and not report['send_ready']
    raw=draft();raw.update(account_access_reviewed=True,free_tier_reviewed=True,free_tier_verified_on=raw['verified_on'])
    report=offline_report(raw)
    assert '本人申告' in report['account_access'] and '実際の利用権限は未確認' in report['free_tier']


def test_probe_access_does_not_enable_generation_or_modify_flags(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);raw=draft();before=copy.deepcopy(raw);old_story=service.stories.path.read_bytes()
    with pytest.raises(ValueError):quote(raw)
    initial=service.store.load();result=execute(service,probe,raw=raw)
    assert result['state']=='success' and len(calls)==1 and calls[0]==probe_body()
    data=service.store.load();assert data['card'] is None and data['candidates']==initial['candidates'] and not data['adoptions']
    assert service.stories.path.read_bytes()==old_story and raw==before
    assert offline_report(raw)['account_access']=='未確認' and offline_report(raw)['free_tier']=='未確認'
    assert 'TEST-PRIVATE-KEY' not in service.store.path.read_text()
    assert open_budget(service.directory).request_status(result['diagnosis_id'])['state']=='settled'


@pytest.mark.parametrize('change',[{'commercial_use_reviewed':False},{'maximum_billing_reviewed':False},{'external_data_reviewed':False},
    {'verified_on':''},{'fx_jpy_per_usd':None},{'tax_multiplier':None},{'input_per_million_usd':'0'},{'model':'different'}, {'max_input_tokens':10}, {'thinking_budget':100}])
def test_unconfirmed_cost_or_terms_never_reach_probe(ai_workspace,monkeypatch,change):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);raw=draft();raw.update(change)
    with pytest.raises(ValueError):execute(service,probe,raw=raw)
    assert not calls and not diagnostic_history(service)


def test_consent_flag_and_revision_required_once(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);raw=draft();plan=preview(service,raw)
    with pytest.raises(ValueError):run(service,raw,probe=probe,expected_revision=service.store.load()['revision'])
    monkeypatch.delenv('MANGA_ENABLE_PAID_STORY')
    with pytest.raises(ValueError):run(service,raw,probe=probe,approval=plan['approval_key'],expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1')
    run(service,raw,probe=probe,approval=plan['approval_key'],expected_revision=service.store.load()['revision'])
    with pytest.raises((ValueError,StorageError)):run(service,raw,probe=probe,approval=plan['approval_key'],expected_revision=service.store.load()['revision'])
    assert len(calls)==1


@pytest.mark.parametrize('code,reason',[(400,'bad_request'),(401,'unauthorized'),(403,'forbidden'),(404,'not_found'),(429,'rate_limited'),(500,'server_error'),(302,'http_error')])
def test_http_errors_are_sanitized_retained_and_not_retried(ai_workspace,monkeypatch,code,reason):
    import manga.story_ai_gemini as module
    service=ai_workspace;prepare(service,monkeypatch);calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request);raise HTTPError('https://secret.invalid/TEST-PRIVATE-KEY',code,'TEST-PRIVATE-KEY',{},io.BytesIO(b'TEST-PRIVATE-KEY'))
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    result=execute(service,Probe());assert result['reason']==reason
    ledger=open_budget(service.directory);assert ledger.request_status(result['diagnosis_id'])['state']=='uncertain'
    with pytest.raises(ValueError):execute(service,Probe())
    assert len(calls)==1 and 'TEST-PRIVATE-KEY' not in service.store.path.read_text()
    assert not list(service.store.root.glob('story_ai/diagnostics/*.json'))


def test_timeout_reconciles_without_resend_then_explicit_new_diagnosis(ai_workspace,monkeypatch):
    service=ai_workspace
    def fail(*a):raise TimeoutError('TEST-PRIVATE-KEY')
    probe,calls=prepare(service,monkeypatch,send=fail);result=execute(service,probe)
    assert result['state']=='uncertain'
    with pytest.raises(ValueError):recover(service,result['diagnosis_id'])
    ledger=open_budget(service.directory);ledger.reconcile_no_charge(result['diagnosis_id'],confirmed_no_charge=True)
    assert recover(service,result['diagnosis_id'])['state']=='unavailable'
    good,_=prepare(service,monkeypatch);assert execute(service,good)['state']=='success' and len(calls)==1


@pytest.mark.parametrize('field',['model','usage','body'])
def test_invalid_results_do_not_claim_permissions(ai_workspace,monkeypatch,field):
    def mutate(*a):
        raw=response()
        if field=='model':raw['modelVersion']='TEST-PRIVATE-KEY'
        elif field=='usage':raw['usageMetadata']={}
        else:raw['candidates'][0]['content']['parts'][0]['text']='{"ok":1,"secret":"TEST-PRIVATE-KEY"}'
        return raw
    service=ai_workspace;probe,calls=prepare(service,monkeypatch,send=mutate);result=execute(service,probe)
    assert result['state']!='success' and len(calls)==1
    for p in service.store.root.glob('story_ai/diagnostics/*.json'):assert 'TEST-PRIVATE-KEY' not in p.read_text()
    assert not service.store.load()['card']


@pytest.mark.parametrize('scope',['project','monthly'])
def test_shared_budget_stops_before_sending(ai_workspace,monkeypatch,scope):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);ledger=open_budget(service.directory)
    for i in range(1 if scope=='project' else 10):
        ledger.reserve(project_id=service.project_id if scope=='project' else str(uuid4()),request_id=str(uuid4()),provider='test',model='test',kind='test',max_jpy='500')
    with pytest.raises(ValueError,match='残額'):execute(service,probe)
    assert not calls


def test_concurrent_diagnoses_do_not_share_approval(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);raw=draft();plan=preview(service,raw);revision=service.store.load()['revision']
    def work(_):
        try:return run(service,raw,approval=plan['approval_key'],expected_revision=revision,probe=probe)
        except (ValueError,StorageError):return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,range(2)))
    assert len(calls)==1 and sum(r is not None for r in results)==1


def test_save_failure_recovers_receipt_without_network(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);original=service.store.save
    def fail(data,kind,**kw):
        if kind=='gemini_connection_diagnostic' and kw['state']=='success':raise OSError('disk full')
        return original(data,kind,**kw)
    monkeypatch.setattr(service.store,'save',fail)
    with pytest.raises(OSError):execute(service,probe)
    monkeypatch.setattr(service.store,'save',original);record=diagnostic_history(service)[0]
    assert record['state']=='processing'
    assert recover(service,record['diagnosis_id'])['state']=='success' and len(calls)==1
    assert recover(service,record['diagnosis_id'])['state']=='success' and len(calls)==1


def test_fixed_probe_context_cannot_generate_story(ai_workspace,monkeypatch):
    from manga.budget import BudgetedExecutor
    from manga.story_ai_gemini import diagnostic_quote
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);identifier=str(uuid4());ledger=open_budget(service.directory)
    def operation():
        # 通常生成はaccount_access_reviewed=falseを今まで通り拒否する。
        request=dict(id=identifier,card=draft())
        with pytest.raises(ValueError):GeminiStoryAI(send=lambda *a:pytest.fail('generation bypass')).generate(request)
        return probe.generate(identifier)
    result=BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=identifier,provider=PROVIDER,model=MODEL,kind='diagnostic',
        price_quote=diagnostic_quote(draft()),usage_limits=LIMITS,operation=operation,actual_charge_jpy=lambda _:'.01')
    assert result['response_valid'] and len(calls)==1


def test_verified_free_is_self_attestation_not_model_grant(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);raw=draft()
    with pytest.raises(ValueError,match='無料枠'):preview(service,raw,'verified-free')
    raw.update(free_tier_reviewed=True,free_tier_verified_on=raw['verified_on']);monkeypatch.delenv('MANGA_ENABLE_PAID_STORY');monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    result=execute(service,probe,raw=raw,mode='verified-free')
    assert result['state']=='success' and not raw['account_access_reviewed']
    assert float(result['maximum_jpy'])>0 and open_budget(service.directory).summary(service.project_id)['project_used_jpy']>0


def test_fixed_probe_cannot_send_other_text_even_with_claim(ai_workspace,monkeypatch):
    from manga.budget import BudgetedExecutor
    from manga.budget.guard import claim_provider_call
    from manga.voice_models import digest
    from manga.story_ai_gemini import diagnostic_quote,transport
    service=ai_workspace;prepare(service,monkeypatch);ledger=open_budget(service.directory)
    body=probe_body();body['contents'][0]['parts'][0]['text']='not an approved diagnostic'
    def operation():
        claim_provider_call(PROVIDER,MODEL,payload_sha256=digest(body))
        return transport(body,'TEST-PRIVATE-KEY')
    with pytest.raises(ValueError,match='固定'):
        BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=str(uuid4()),provider=PROVIDER,model=MODEL,kind='test',
            price_quote=diagnostic_quote(draft()),usage_limits=LIMITS,operation=operation,actual_charge_jpy=lambda _:'.01')


def test_settled_missing_receipt_does_not_claim_success_or_block_forever(ai_workspace,monkeypatch):
    service=ai_workspace
    def fail(*a):raise TimeoutError('TEST-PRIVATE-KEY')
    probe,calls=prepare(service,monkeypatch,send=fail);result=execute(service,probe)
    ledger=open_budget(service.directory);ledger.settle(result['diagnosis_id'],actual_jpy='0',confirmed_charge=True)
    recovered=recover(service,result['diagnosis_id']);assert recovered['state']=='unavailable' and recovered['reason']=='invalid_response'
    good,_=prepare(service,monkeypatch);assert execute(service,good)['state']=='success'


def test_diagnostic_ledger_cannot_be_reset_before_normal_generation(ai_workspace,monkeypatch,tmp_path):
    from test_story_ai_gemini import card
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);execute(service,probe)
    service.configure(service.store.load()['settings'],card(),expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'other-ledger.sqlite3'))
    from manga.story_ai_gemini import GeminiStoryAI
    service.adapter=GeminiStoryAI(send=lambda *a:pytest.fail('no generation'),key='test')
    with pytest.raises(ValueError,match='保存先'):job(service,'concepts','gemini-text')
    assert len(calls)==1


def test_normal_generation_card_cannot_use_diagnostic_execution_for_story(ai_workspace,monkeypatch):
    from manga.budget import BudgetedExecutor
    from manga.story_ai_gemini import diagnostic_quote
    from manga.story_ai_requests import next_request
    from test_story_ai_gemini import card
    service=ai_workspace;prepare(service,monkeypatch);normal_card=card();normal_card['max_output_tokens']=256
    service.configure(service.store.load()['settings'],normal_card,expected_revision=service.store.load()['revision'])
    service.adapter=GeminiStoryAI(send=lambda *a:pytest.fail('diagnostic claim used for a story'),key='test')
    created=job(service,'concepts','gemini-text');request=next_request(service,created['id'])
    ledger=open_budget(service.directory)
    with pytest.raises(ValueError,match='固定'):
        BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=request['id'],provider=PROVIDER,model=MODEL,kind='test',
            price_quote=diagnostic_quote(draft()),usage_limits=LIMITS,operation=lambda:service.adapter.generate(request),actual_charge_jpy=lambda _:'.01')


def test_pending_story_adoption_blocks_diagnostic_metadata_until_recovery(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);write=service.store.write
    pending=service.store.safe('story_ai/adoption_pending.json')
    def interrupted_adoption(path,content):
        result=write(path,content)
        if path.endswith('.json'):pending.write_text('{"state":"pending"}')
        return result
    monkeypatch.setattr(service.store,'write',interrupted_adoption)
    with pytest.raises(StorageError,match='採用'):execute(service,probe)
    record=diagnostic_history(service)[0]
    assert record['state']=='processing' and len(calls)==1
    # テスト用の採用保存が復旧した状態を模擬。診断はAPIを再送せず記録のみ再開する。
    pending.unlink();monkeypatch.setattr(service.store,'write',write)
    assert recover(service,record['diagnosis_id'])['state']=='success' and len(calls)==1
