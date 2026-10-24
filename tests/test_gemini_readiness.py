import copy
import json
from contextvars import copy_context
from datetime import date,timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pytest

from manga.budget import BudgetedExecutor
from manga.budget.config import open_budget
from manga.budget.guard import current_execution
from manga.image_ai_live_provider import GeminiImageAdapter,LiveError,network_allowed
from manga.image_ai_live_pricing import bounds,price_quote
from manga.image_ai_live_plan import prepare
from manga.image_ai_readiness import assess,diagnostic,load_evidence,REQUIRED
from manga.storage import StorageError
from image_ai_fixtures import image_workspace
from test_image_ai_live import setup_live,create_live


def test_confirmed_cloud_sdk_facts_do_not_authorize_developer_api():
    result=assess()
    assert result['decision']=='no-go' and not result['evidence_ready']
    assert {b['id'] for b in result['blockers']}==REQUIRED
    assert {f['scope'] for f in result['facts']}=={'sdk','vertex','cloud-billing'}
    assert all(not s['retrieved'] for s in result['sources'] if s['scope']=='developer-api')
    assert result['maximum_jpy'] is None and result['reference_cost']['usd']=='0.0336'
    assert Decimal(result['reference_cost']['jpy'])==Decimal('5.9136')


@pytest.mark.parametrize('change',['missing','duplicate','wrong_model','wrong_scope','unretrieved_confirmed','external_url','query','credentials','bad_hash','bad_price','maximum','wrong_date','future_date','truthy','malformed'])
def test_evidence_incomplete_or_unsafe_fails_closed(change):
    raw=load_evidence()
    if change=='missing': raw['requirements'].pop()
    elif change=='duplicate': raw['requirements'][0]=copy.deepcopy(raw['requirements'][1])
    elif change=='wrong_model': raw['model']='other-image-model'
    elif change=='wrong_scope': raw['requirements'][0].update(status='confirmed',source_ids=['vertex-pricing'])
    elif change=='unretrieved_confirmed': raw['requirements'][0]['status']='confirmed'
    elif change=='external_url': raw['sources'][0]['url']='https://example.com/pricing'
    elif change=='query': raw['sources'][0]['url']+='?key=TEST_PRIVATE_VALUE'
    elif change=='credentials': raw['sources'][0]['url']='https://secret@ai.google.dev/docs'
    elif change=='bad_hash': raw['sources'][-1]['sha256']='0'
    elif change=='bad_price': raw['reference_cost']['jpy']='0'
    elif change=='maximum': raw['reference_cost']['maximum_jpy']='5.9136'
    elif change=='wrong_date': raw['sources'][0]['checked_on']='2000-01-01'
    elif change=='future_date': raw['verified_on']='not-a-date'
    elif change=='truthy': raw['sources'][0]['retrieved']='true'
    else: raw={}
    with pytest.raises(ValueError,match='送信'): assess(raw)


def test_source_reverification_date_expired_or_future_is_blocker():
    raw=load_evidence(); day=date.fromisoformat(raw['verified_on'])
    for today in (day-timedelta(days=1),day+timedelta(days=31)):
        result=assess(raw,today=today)
        assert any(b['id']=='expired' for b in result['blockers'])
        assert not result['evidence_ready']


def test_diagnostic_reads_neither_user_data_nor_budget_and_hides_keys(tmp_path,monkeypatch):
    secret='SENTINEL_PRIVATE_VALUE_NOT_FOR_REPORT'
    monkeypatch.setenv('GEMINI_API_KEY',secret); monkeypatch.setenv('MANGA_ENABLE_IMAGE_API','1')
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'do-not-create.sqlite3'))
    monkeypatch.setenv('MANGA_DATA_DIR',str(tmp_path/'do-not-read'))
    def forbidden(*a,**k): raise AssertionError('Readiness must not open budget or project data')
    monkeypatch.setattr('manga.budget.config.open_budget',forbidden)
    report=diagnostic(); content=json.dumps(report,ensure_ascii=False)
    assert report['authentication_configured'] and report['feature_flag_enabled'] and not report['production_enabled']
    assert report['api_calls']==report['budget_reservations']==0 and secret not in content
    assert not list(tmp_path.iterdir())


def test_json_or_legacy_boolean_changes_cannot_remove_evidence_gate(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','SENTINEL_PRIVATE_VALUE'); monkeypatch.setenv('MANGA_ENABLE_IMAGE_API','1')
    monkeypatch.setattr('manga.image_ai_live_pricing.NETWORK_VERIFIED',True)
    original=__import__('manga.image_ai_live_pricing',fromlist=['research']).research()
    original.update(network_enabled=True,maximum_charge_verified=True)
    monkeypatch.setattr('manga.image_ai_live_pricing.research',lambda:original)
    with pytest.raises(LiveError,match='最大'): network_allowed()
    assert not diagnostic()['global_prerequisites_met']


def test_missing_or_corrupt_evidence_file_never_enables(tmp_path,monkeypatch):
    from manga import image_ai_readiness as readiness
    path=tmp_path/'report.json'; monkeypatch.setattr(readiness,'REPORT',path)
    with pytest.raises(ValueError): network_allowed()
    path.write_text('{not json')
    with pytest.raises(ValueError): network_allowed()
    target=tmp_path/'real.json'; target.write_text('{}'); path.unlink(); path.symlink_to(target)
    with pytest.raises(ValueError): network_allowed()


def test_adapter_single_request_per_budget_claim_even_on_direct_double_call(image_workspace):
    values=setup_live(image_workspace); service,plan,card,_,http=values
    executor=BudgetedExecutor(open_budget(service.directory))
    _,body=prepare(service,plan,card,values[3])
    def twice():
        service.live_adapter.send(card,body)
        return service.live_adapter.send(card,body)
    with pytest.raises(ValueError,match='1リクエスト'):
        executor.run(project_id=service.project_id,request_id='single-call-only',provider='gemini-live',model=card['model'],kind='image',
                     price_quote=price_quote(card),usage_limits=bounds(card),operation=twice,actual_charge_jpy=lambda r:'5.9224')
    assert len(http.calls)==1 and current_execution() is None
    assert executor.ledger.request_status('single-call-only')['state']=='uncertain'


def test_copied_context_cannot_claim_two_requests_concurrently(image_workspace):
    values=setup_live(image_workspace); service,plan,card,_,http=values
    _,body=prepare(service,plan,card,values[3])
    def operation():
        contexts=[copy_context(),copy_context()]
        def send(context):
            try: return context.run(service.live_adapter.send,card,body)
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: responses=list(pool.map(send,contexts))
        assert sum(r is not None for r in responses)==1
        return next(r for r in responses if r is not None)
    executor=BudgetedExecutor(open_budget(service.directory))
    executor.run(project_id=service.project_id,request_id='concurrent-single-call',provider='gemini-live',model=card['model'],kind='image',
                 price_quote=price_quote(card),usage_limits=bounds(card),operation=operation,actual_charge_jpy=lambda r:'5.9224')
    assert len(http.calls)==1 and current_execution() is None


def test_recheck_source_after_budget_claim_before_http(image_workspace,monkeypatch):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,quote=create_live(values)
    from manga.budget import BudgetLedger
    original=BudgetLedger.claim_execution
    def mutate(self,*args,**kwargs):
        claim=original(self,*args,**kwargs)
        service.artwork.save_style('予約直後に変更した別のフルカラー作風',expected_revision=service.artwork.load()['revision'])
        return claim
    monkeypatch.setattr(BudgetLedger,'claim_execution',mutate)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert not http.calls and service.store.load()['jobs'][0]['api_calls']==0
    status=open_budget(service.directory).request_status(job['id'])
    assert status['state']=='uncertain' and status['quote_jpy']==Decimal(quote['max_jpy'])
    with pytest.raises(ValueError): service.run(job['id'],approved=True)


@pytest.mark.parametrize('change',['zero_input','zero_image_output','bool_cache','bool_tool'])
def test_inconsistent_billing_counters_never_assumed_free(image_workspace,change):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,quote=create_live(values)
    usage=http.response['usageMetadata']
    if change=='zero_input': usage.update(promptTokenCount=0,totalTokenCount=1120)
    elif change=='zero_image_output': usage.update(candidatesTokenCount=0,totalTokenCount=100)
    elif change=='bool_cache': usage['cachedContentTokenCount']=False
    else: usage['toolUsePromptTokenCount']=False
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert open_budget(service.directory).request_status(job['id'])['state']=='uncertain'
    assert not service.store.load()['candidates'] and len(http.calls)==1


def test_readonly_cli_no_go_exit_status_and_no_overwrite(tmp_path,monkeypatch,capsys):
    from scripts.check_gemini_image_readiness import main
    monkeypatch.setenv('GEMINI_API_KEY','SENTINEL_PRIVATE_VALUE')
    output=tmp_path/'readiness.json'
    assert main(['--output',str(output)])==2
    report=json.loads(output.read_text()); before=output.read_bytes()
    assert report['decision']=='no-go' and not report['production_enabled']
    with pytest.raises(SystemExit) as error: main(['--output',str(output)])
    assert error.value.code==1 and output.read_bytes()==before
    assert 'SENTINEL_PRIVATE_VALUE' not in capsys.readouterr().out
