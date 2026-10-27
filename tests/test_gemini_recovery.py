"""段階ログと確定請求の照合。HTTP/請求は全てフェイク、作品はtmp内のみ。"""
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from manga.budget import BudgetLedger, ReservationConflict
from manga.budget.config import open_budget
from manga.gemini_diagnostic_trace import history, Trace, DiagnosticStorageError
from manga.gemini_diagnostics import Probe, diagnostic_history, recover
from story_ai_fixtures import ai_workspace
from test_gemini_diagnostics import prepare, execute, response

SECRET='TEST-SECRET-NEVER-PERSIST'


def fake_http(monkeypatch, *, code=200, body=None, opening_error=None, reading_error=None):
    import manga.story_ai_gemini as gemini
    calls=[]
    class Reply:
        status=code
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read1(self,n):
            if reading_error:raise reading_error
            return self.data.read(n)
    class Opener:
        def open(self,request,timeout):
            calls.append(1)
            if opening_error:raise opening_error
            if code!=200:raise HTTPError('https://example.invalid/'+SECRET,code,SECRET,{'secret':SECRET},io.BytesIO(SECRET.encode()))
            reply=Reply();reply.data=io.BytesIO(json.dumps(response()).encode() if body is None else body);return reply
    monkeypatch.setattr(gemini,'build_opener',lambda *a:Opener())
    return calls


def assert_private(service):
    for path in service.store.root.rglob('*'):
        if path.is_file():assert SECRET.encode() not in path.read_bytes()


def test_real_transport_phases_are_recorded_without_secret(ai_workspace,monkeypatch):
    service=ai_workspace;prepare(service,monkeypatch);calls=fake_http(monkeypatch)
    result=execute(service,Probe());events=history(service,result['diagnosis_id'])
    assert result['state']=='success' and calls==[1]
    completed=[r['stage'] for r in events if r['outcome']=='completed']
    assert completed==['pre_send','sending','http_response','response_validation','result_storage','budget_settlement','history_storage']
    assert next(r for r in events if r['stage']=='http_response')['http_status']==200
    assert all(r['created_at'].endswith('+09:00') for r in events)
    assert_private(service)


@pytest.mark.parametrize('code,reason',[(401,'unauthorized'),(403,'forbidden'),(404,'not_found'),(429,'rate_limited'),(500,'server_error'),(503,'server_error')])
def test_http_rejection_is_received_not_a_guessed_network_error(ai_workspace,monkeypatch,code,reason):
    service=ai_workspace;prepare(service,monkeypatch);calls=fake_http(monkeypatch,code=code)
    result=execute(service,Probe());events=history(service,result['diagnosis_id'])
    assert result['reason']==reason and calls==[1]
    assert any(e['stage']=='http_response' and e['http_status']==code and e['reason']==reason for e in events)
    assert not any(e['stage']=='response_validation' for e in events)
    book=open_budget(service.directory);status=book.request_status(result['diagnosis_id'])
    assert status['state']=='uncertain' and status['quote_jpy']>0 and book.summary(service.project_id)['blocked']
    with pytest.raises(ValueError):execute(service,Probe())
    with pytest.raises(ValueError):recover(service,result['diagnosis_id'])
    assert calls==[1];assert_private(service)


@pytest.mark.parametrize('point,reason',[('open','connection_timeout'),('body','response_timeout'),('network','network_error'),('json','invalid_response')])
def test_timeout_and_format_classification(ai_workspace,monkeypatch,point,reason):
    service=ai_workspace;prepare(service,monkeypatch)
    calls=fake_http(monkeypatch,opening_error=URLError(TimeoutError(SECRET)) if point=='open' else URLError(SECRET) if point=='network' else None,
        reading_error=TimeoutError(SECRET) if point=='body' else None,body=SECRET.encode() if point=='json' else None)
    result=execute(service,Probe());events=history(service,result['diagnosis_id'])
    assert result['reason']==reason and calls==[1]
    assert any(e['outcome']=='failed' and e['reason']==reason for e in events)
    assert any(e['stage']=='http_response' for e in events)==(point in ('body','json'))
    assert_private(service)


@pytest.mark.parametrize('field',['model','usage','text'])
def test_response_validation_is_distinct_from_http_success(ai_workspace,monkeypatch,field):
    service=ai_workspace;prepare(service,monkeypatch);raw=response()
    if field=='model':raw['modelVersion']='another-model'
    elif field=='usage':raw['usageMetadata']={}
    else:raw['candidates'][0]['content']['parts'][0]['text']='{"ok":false}'
    fake_http(monkeypatch,body=json.dumps(raw).encode())
    result=execute(service,Probe());events=history(service,result['diagnosis_id'])
    assert result['state']!='success'
    assert any(e['stage']=='http_response' and e['http_status']==200 for e in events)
    assert any(e['stage']=='response_validation' and e['outcome']=='failed' for e in events)


def test_storage_failure_has_stage_and_preserves_reservation(ai_workspace,monkeypatch):
    service=ai_workspace;prepare(service,monkeypatch);calls=fake_http(monkeypatch);write=service.store.write
    def fail(path,content):
        if path.endswith('.json'):raise OSError(SECRET)
        return write(path,content)
    monkeypatch.setattr(service.store,'write',fail)
    result=execute(service,Probe());events=history(service,result['diagnosis_id'])
    assert result['reason']=='storage_error' and calls==[1]
    assert any(e['stage']=='result_storage' and e['outcome']=='failed' for e in events)
    assert open_budget(service.directory).request_status(result['diagnosis_id'])['state']=='uncertain'
    assert_private(service)


def test_logging_failure_before_wire_stops_without_resetting_history(ai_workspace,monkeypatch):
    service=ai_workspace;prepare(service,monkeypatch);calls=fake_http(monkeypatch);write=service.store.write
    def fail(path,content):
        if path.endswith('.stage'):raise OSError(SECRET)
        return write(path,content)
    monkeypatch.setattr(service.store,'write',fail)
    with pytest.raises(DiagnosticStorageError):execute(service,Probe())
    record=diagnostic_history(service)[0]
    assert not calls and open_budget(service.directory).request_status(record['diagnosis_id']) is None
    assert recover(service,record['diagnosis_id'])['reason']=='not_sent'
    assert_private(service)


def test_old_record_is_not_backfilled_and_trace_read_is_read_only(ai_workspace,monkeypatch):
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);record=execute(service,probe)
    directory=service.store.safe(f"story_ai/diagnostics/{record['diagnosis_id']}.trace")
    for path in directory.iterdir():path.unlink()  # テスト作品で旧記録を模擬。
    directory.rmdir();before=service.store.path.read_bytes()
    assert history(service,record['diagnosis_id'])==[] and service.store.path.read_bytes()==before
    assert len(calls)==1


def pending(book, *, work=None, quote='23.8128', uncertain=True):
    work=work or str(uuid4());key=str(uuid4())
    book.reserve(project_id=work,request_id=key,provider='gemini-text',model='gemini-2.5-flash-lite',kind='connection-diagnostic',max_jpy=quote)
    if uncertain:book.mark_uncertain(key)
    return work,key


def test_details_are_cross_project_exact_and_do_not_change_money(tmp_path):
    book=BudgetLedger(tmp_path/'book.sqlite3');work,key=pending(book,uncertain=False);other,key2=pending(book)
    book.mark_uncertain(key)
    before=book.summary(work);rows=book.pending_requests()
    assert {r['request_id'] for r in rows}=={key,key2} and str(rows[0]['quote_jpy'])=='23.8128'
    assert not any('claim_id' in r for r in rows) and book.summary(work)==before


@pytest.mark.parametrize('options',[
    {},{'confirmed_statement':True},{'confirmed_request':True},
    {'outcome':'unknown'},{'actual_jpy':'NaN'},{'actual_jpy':'-1'},{'actual_jpy':'0'},
    {'charged_at':None},{'project_id':str(uuid4())},
    {'charged_at':datetime.now(ZoneInfo('Asia/Tokyo'))+timedelta(days=2)},
])
def test_manual_reconcile_requires_known_charge_and_exact_target(tmp_path,options):
    book=BudgetLedger(tmp_path/'book.sqlite3');work,key=pending(book)
    args=dict(project_id=work,outcome='charge',actual_jpy='0.1',charged_at=datetime.now(ZoneInfo('Asia/Tokyo')),
              confirmed_statement=True,confirmed_request=True)
    if not options:args['confirmed_statement']=False;args['confirmed_request']=False
    elif options=={'confirmed_statement':True}:args['confirmed_request']=False
    elif options=={'confirmed_request':True}:args['confirmed_statement']=False
    args.update(options)
    with pytest.raises(ValueError):book.reconcile_manual(key,**args)
    assert book.request_status(key)['state']=='uncertain' and not book.reconciliation_history()


@pytest.mark.parametrize('outcome',['charge','no_charge'])
def test_manual_reconcile_once_with_audit_keeps_other_reservations(tmp_path,outcome):
    book=BudgetLedger(tmp_path/'book.sqlite3');work,key=pending(book,uncertain=False);_,other=pending(book,work=work)
    book.mark_uncertain(key)
    args=dict(project_id=work,outcome=outcome,actual_jpy='0.12' if outcome=='charge' else None,
              charged_at=datetime.now(ZoneInfo('Asia/Tokyo')),confirmed_statement=True,confirmed_request=True)
    def apply(_):
        try:book.reconcile_manual(key,**args);return True
        except ReservationConflict:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sum(pool.map(apply,range(2)))==1
    assert book.request_status(key)['state']=='settled' and book.request_status(other)['state']=='uncertain'
    assert len(book.reconciliation_history())==1 and book.summary(work)['blocked']


def test_audit_save_failure_rolls_back_money_and_preserves_block(tmp_path):
    book=BudgetLedger(tmp_path/'book.sqlite3');work,key=pending(book)
    with sqlite3.connect(book.path) as db:
        db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON api_budget_reconciliations BEGIN SELECT RAISE(ABORT,'offline disk failure'); END")
    with pytest.raises(sqlite3.Error):
        book.reconcile_manual(key,project_id=work,outcome='no_charge',confirmed_statement=True,confirmed_request=True)
    assert book.request_status(key)['state']=='uncertain' and not book.reconciliation_history()
    assert book.summary(work)['project_used_jpy']==book.request_status(key)['quote_jpy']


def test_overrun_is_recorded_at_actual_amount_and_still_blocks(tmp_path):
    book=BudgetLedger(tmp_path/'book.sqlite3');work,key=pending(book)
    book.reconcile_manual(key,project_id=work,outcome='charge',actual_jpy='501',charged_at=datetime.now(ZoneInfo('Asia/Tokyo')),
                          confirmed_statement=True,confirmed_request=True)
    assert book.summary(work)['project_used_jpy']==501 and book.summary(work)['blocked']
    assert book.summary(work)['unreviewed_overrun_count']==1


def test_old_ledger_additive_upgrade_preserves_pending_and_execution_tables(tmp_path):
    path=tmp_path/'book.sqlite3';book=BudgetLedger(path);work,key=pending(book)
    with sqlite3.connect(path) as db:
        before=db.execute('SELECT * FROM api_budget_requests').fetchall()
        db.execute('DROP TABLE api_budget_reconciliations');db.execute('DROP TABLE api_budget_recovery_version')
    loaded=BudgetLedger(path)
    with sqlite3.connect(path) as db:assert db.execute('SELECT * FROM api_budget_requests').fetchall()==before
    assert loaded.request_status(key)['state']=='uncertain' and loaded.summary(work)['blocked']
    with sqlite3.connect(path) as db:db.execute('DROP TABLE api_budget_reconciliations')
    with pytest.raises(ValueError):BudgetLedger(path)


def test_reconciliation_does_not_turn_failed_legacy_diagnostic_into_success(ai_workspace,monkeypatch):
    service=ai_workspace
    def fail(*a):raise TimeoutError(SECRET)
    probe,calls=prepare(service,monkeypatch,send=fail);result=execute(service,probe)
    book=open_budget(service.directory)
    book.reconcile_manual(result['diagnosis_id'],project_id=service.project_id,outcome='no_charge',confirmed_statement=True,confirmed_request=True)
    recovered=recover(service,result['diagnosis_id'])
    assert recovered['state']=='unavailable' and len(calls)==1 and service.store.load()['card'] is None
