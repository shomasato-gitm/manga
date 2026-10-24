"""Offline lifecycle, storage failures and real multi-process reservations."""
import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from budget_fakes import offline_quote
from manga.budget import BudgetLedger, BudgetedExecutor, BudgetExceeded, ReservationConflict

JST = ZoneInfo('Asia/Tokyo')


def run(book, *, work=None, key=None, amount='20', actual='5', operation=None, **changes):
    options = dict(project_id=work or str(uuid4()), request_id=key or str(uuid4()),
        provider='offline', model='fake-model', kind='tts', quote_jpy=amount,
        operation=operation or (lambda: 'offline response'), actual_charge_jpy=lambda _: actual,
        **offline_quote(amount=amount))
    options.update(changes)
    return BudgetedExecutor(book).run(**options)


def _reserve_worker(args):
    path, work, amount, provider, provider_limit = args
    book = BudgetLedger(path,provider_monthly_limits_jpy=provider_limit)
    try:
        book.reserve(project_id=work,request_id=str(uuid4()),provider=provider,
                     model='fake',kind='tts',max_jpy=amount)
        return True
    except BudgetExceeded:
        return False


def _execute_worker(args):
    path, work, key, counter = args
    def operation():
        with Path(counter).open('a') as file:
            file.write('local fake call\n')
        return 'offline response'
    try:
        run(BudgetLedger(path),work=work,key=key,operation=operation)
        return True
    except ReservationConflict:
        return False


@pytest.mark.parametrize('scope', ['work','monthly','provider'])
def test_caps_hold_across_spawned_processes(tmp_path, scope):
    path = str(tmp_path/'budget.sqlite3')
    work = str(uuid4())
    limits = {'offline':'150'} if scope=='provider' else {}
    amount = '500' if scope=='monthly' else '30'
    expected = {'work':16,'monthly':10,'provider':5}[scope]
    args = [(path, str(uuid4()) if scope=='monthly' else work, amount,'offline',limits) for _ in range(24)]
    with ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn')) as pool:
        outcomes = list(pool.map(_reserve_worker,args))
    assert sum(outcomes)==expected
    book = BudgetLedger(path,provider_monthly_limits_jpy=limits)
    assert book.summary(work)['monthly_used_jpy']==expected*int(amount)


def test_same_request_executes_once_across_processes(tmp_path):
    path, work, key = str(tmp_path/'budget.sqlite3'), str(uuid4()), str(uuid4())
    counter = str(tmp_path/'local-calls.txt')
    with ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn')) as pool:
        results = list(pool.map(_execute_worker,[(path,work,key,counter)]*8))
    assert sum(results)==1
    assert Path(counter).read_text().splitlines()==['local fake call']
    assert BudgetLedger(path).summary(work)['project_used_jpy']==5


def test_running_operation_cannot_cancel_or_settle_its_reservation_without_claim(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work, key = str(uuid4()),str(uuid4())
    def operation():
        with pytest.raises(ReservationConflict):book.cancel_not_sent(key)
        with pytest.raises(ReservationConflict):book.settle(key,actual_jpy='0')
        return 'offline'
    run(book,work=work,key=key,operation=operation)
    assert book.summary(work)['project_used_jpy']==5


@pytest.mark.parametrize('invalid', ['NaN','Infinity','-Infinity','-1',None])
def test_invalid_reported_cost_holds_quote_and_blocks_new_work(tmp_path, invalid):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work = str(uuid4())
    with pytest.raises(ValueError):run(book,work=work,actual=invalid)
    assert book.summary(work)['project_used_jpy']==20
    assert book.summary(work)['uncertain_count']==1
    with pytest.raises(BudgetExceeded):run(book)


@pytest.mark.parametrize('interrupt', [TimeoutError,KeyboardInterrupt,SystemExit])
def test_provider_interrupt_holds_reservation(tmp_path, interrupt):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work = str(uuid4())
    def fail():raise interrupt('offline interruption')
    with pytest.raises(interrupt):run(book,work=work,operation=fail)
    assert book.summary(work)['requests_by_status']=={'uncertain':1}
    assert book.summary(work)['project_used_jpy']==20


def test_settlement_failure_is_not_retried_and_can_be_manually_reconciled(tmp_path,monkeypatch):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work,key,calls = str(uuid4()),str(uuid4()),[]
    original = book.settle
    def fail(*a,**k):raise sqlite3.OperationalError('simulated disk full')
    monkeypatch.setattr(book,'settle',fail)
    with pytest.raises(sqlite3.OperationalError):run(book,work=work,key=key,operation=lambda:calls.append(True))
    assert calls==[True]
    assert book.summary(work)['requests_by_status']=={'uncertain':1}
    monkeypatch.setattr(book,'settle',original)
    with pytest.raises(ReservationConflict):book.settle(key,actual_jpy='5')
    book.settle(key,actual_jpy='5',confirmed_charge=True)
    with pytest.raises(ReservationConflict):run(book,work=work,key=key,operation=lambda:calls.append(True))
    assert calls==[True] and book.summary(work)['project_used_jpy']==5


def test_failed_uncertain_marker_preserves_claim_across_restart(tmp_path,monkeypatch):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work,key = str(uuid4()),str(uuid4())
    def fail(*a,**k):raise sqlite3.OperationalError('offline storage failure')
    monkeypatch.setattr(book,'mark_uncertain',fail)
    with pytest.raises(sqlite3.OperationalError):run(book,work=work,key=key,actual=None)
    restarted = BudgetLedger(book.path)
    assert restarted.summary(work)['project_used_jpy']==20
    with pytest.raises(ReservationConflict):restarted.cancel_not_sent(key)
    with pytest.raises(ReservationConflict):run(restarted,work=work,key=key)
    restarted.mark_uncertain(key)
    restarted.reconcile_no_charge(key,confirmed_no_charge=True)
    assert restarted.summary(work)['project_used_jpy']==0


def test_execution_claim_failure_sends_nothing_and_keeps_reservation(tmp_path,monkeypatch):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work,key,calls = str(uuid4()),str(uuid4()),[]
    def fail(*a,**k):raise OSError('offline lock failure')
    monkeypatch.setattr(book,'claim_execution',fail)
    with pytest.raises(OSError):run(book,work=work,key=key,operation=lambda:calls.append(True))
    assert calls==[] and book.summary(work)['project_used_jpy']==20
    book.cancel_not_sent(key)
    assert book.summary(work)['project_used_jpy']==0


def test_overrun_requires_explicit_review_and_never_erases_actual_cost(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work,key = str(uuid4()),str(uuid4())
    run(book,work=work,key=key,actual='21')
    assert book.summary(work)['blocked']
    with pytest.raises(ValueError):book.acknowledge_overrun(key)
    book.acknowledge_overrun(key,confirmed_reviewed=True,reason='架空の明細と修正後の最大見積を確認')
    assert not book.summary(work)['blocked'] and book.summary(work)['project_used_jpy']==21
    run(book,work=work,amount='479',actual='479')
    with pytest.raises(BudgetExceeded):run(book,work=work,amount='0.000001')


def test_settlement_month_and_provider_carry_are_conservative(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3',provider_monthly_limits_jpy={'offline':'100'})
    work,key = str(uuid4()),str(uuid4())
    oct31 = datetime(2026,10,31,23,59,tzinfo=JST)
    nov1 = oct31+timedelta(minutes=2)
    book.reserve(project_id=work,request_id=key,provider='offline',model='fake',kind='tts',max_jpy='100',now=oct31)
    preview = BudgetedExecutor(book).dry_run(project_id=str(uuid4()),quote_jpy='1',provider='offline',now=nov1)
    assert preview['possible'] is False and preview['network_calls']==0
    book.settle(key,actual_jpy='90',charged_at=nov1)
    assert book.summary(work,now=nov1)['monthly_used_jpy']==90
    assert book.summary(work,now=oct31)['monthly_used_jpy']==0
    assert book.summary(work,now=oct31)['project_used_jpy']==90


@pytest.mark.parametrize('problem', ['expired','future','model','provider','tax','retry','too_small','missing_bounds','missing_key'])
def test_missing_or_invalid_pricing_never_calls_provider(tmp_path,problem):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    calls = []
    options = offline_quote()
    quote = options['price_quote']
    today = datetime.now(JST).date()
    if problem in ('expired','future'):
        day = today+timedelta(days=-31 if problem=='expired' else 1)
        options['price_quote']=replace(quote,rates=replace(quote.rates,verified_on=day.isoformat()))
    elif problem in ('model','provider'):
        options['price_quote']=replace(quote,rates=replace(quote.rates,**{problem:'another'}))
    elif problem=='tax':options['price_quote']=replace(quote,rates=replace(quote.rates,tax_note=''))
    elif problem=='retry':options['usage_limits']['retry_limit']=1
    elif problem=='missing_bounds':options['usage_limits']=None
    elif problem=='missing_key':options['usage_limits'].pop('max_output_tokens')
    elif problem=='too_small':options['price_quote']=replace(quote,tax_multiplier='2')
    with pytest.raises(ValueError):run(book,operation=lambda:calls.append(True),**options)
    assert calls==[]
    assert book.summary(str(uuid4()))['monthly_pending_count']==0


def test_quote_evidence_is_saved_with_reservation(tmp_path):
    import json
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    work,key = str(uuid4()),str(uuid4())
    run(book,work=work,key=key)
    with sqlite3.connect(str(book.path)) as db:
        evidence = json.loads(db.execute('SELECT evidence FROM api_budget_quotes WHERE request_id=?',(key,)).fetchone()[0])
    assert evidence['rates']['provider']=='offline'
    assert evidence['rates']['model']=='fake-model'
    assert evidence['bounds']['max_items']==1 and evidence['max_attempts']==1
    assert evidence['computed_max_jpy']=='20.000000'
    assert evidence['fx_jpy_per_usd']=='1' and evidence['rates']['tax_note']


def test_corrupt_or_missing_ledger_does_not_call_provider(tmp_path):
    path = tmp_path/'budget.sqlite3'
    path.write_bytes(b'not a sqlite database')
    before = path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):BudgetLedger(path)
    assert path.read_bytes()==before
    path.unlink()
    book = BudgetLedger(path)
    path.unlink()
    calls = []
    with pytest.raises(sqlite3.DatabaseError):run(book,operation=lambda:calls.append(True))
    assert calls==[] and not path.exists()
    with pytest.raises(sqlite3.DatabaseError):BudgetLedger(path)
    assert not path.exists()


def test_legacy_ledger_adds_metadata_without_losing_charges(tmp_path):
    path,work,key = tmp_path/'budget.sqlite3',str(uuid4()),str(uuid4())
    book = BudgetLedger(path)
    book.reserve(project_id=work,request_id=key,provider='offline',model='fake',kind='tts',max_jpy='50')
    book.settle(key,actual_jpy='12')
    with sqlite3.connect(str(path)) as db:
        old = db.execute('SELECT * FROM api_budget_requests').fetchall()
        for name in ('api_budget_policy','api_budget_quotes','api_budget_executions','api_budget_overrun_reviews'):
            db.execute('DROP TABLE '+name)
    restarted = BudgetLedger(path)
    with sqlite3.connect(str(path)) as db:
        assert db.execute('SELECT * FROM api_budget_requests').fetchall()==old
    assert restarted.summary(work)['project_used_jpy']==12


def test_missing_management_table_is_not_rebuilt(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    with sqlite3.connect(str(book.path)) as db:db.execute('DROP TABLE api_budget_executions')
    with pytest.raises(ValueError):BudgetLedger(book.path)


def test_legacy_reserved_request_is_not_assumed_unsent(tmp_path):
    path,work,key = tmp_path/'budget.sqlite3',str(uuid4()),str(uuid4())
    book = BudgetLedger(path)
    book.reserve(project_id=work,request_id=key,provider='offline',model='fake',kind='tts',max_jpy='50')
    with sqlite3.connect(str(path)) as db:
        for name in ('api_budget_policy','api_budget_quotes','api_budget_executions','api_budget_overrun_reviews'):
            db.execute('DROP TABLE '+name)
    legacy = BudgetLedger(path)
    assert legacy.summary(work)['project_used_jpy']==50
    assert legacy.summary(work)['blocked']
    with pytest.raises(ReservationConflict):legacy.cancel_not_sent(key)
    legacy.mark_uncertain(key)
    legacy.reconcile_no_charge(key,confirmed_no_charge=True)
    assert legacy.summary(work)['project_used_jpy']==0


def test_reservation_failure_rolls_back_money_and_quote_together(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    with sqlite3.connect(str(book.path)) as db:
        db.execute("CREATE TRIGGER quote_failure BEFORE INSERT ON api_budget_quotes BEGIN SELECT RAISE(ABORT,'offline write failure'); END")
    calls = []
    with pytest.raises(sqlite3.DatabaseError):run(book,operation=lambda:calls.append(True))
    assert calls==[]
    assert book.summary(str(uuid4()))['monthly_used_jpy']==0


def test_quote_source_date_and_amounts_are_validated():
    from manga.budget import PriceRates
    for day in ('yesterday','2026-02-30','20261024'):
        with pytest.raises(ValueError):PriceRates('1','1',source_url='https://example.com/mock',verified_on=day)
    with pytest.raises(ValueError):PriceRates('1','1',source_url='https://token:secret@example.com/mock',verified_on='2026-10-24')
    for bad in ('NaN','Infinity','-1'):
        with pytest.raises(ValueError):replace(offline_quote()['price_quote'],fx_jpy_per_usd=bad).estimate_jpy(max_items=1)


def test_shared_persistent_location_can_be_external(tmp_path,monkeypatch):
    from manga.budget.config import open_budget
    path = tmp_path/'persistent'/'budget.sqlite3'
    monkeypatch.setenv('MANGA_BUDGET_DB',str(path))
    first = open_budget(tmp_path/'one'/'projects')
    second = open_budget(tmp_path/'two'/'projects')
    work,key = str(uuid4()),str(uuid4())
    first.reserve(project_id=work,request_id=key,provider='offline',model='fake',kind='tts',max_jpy='500')
    assert second.summary(work)['project_remaining_jpy']==0
    monkeypatch.setenv('MANGA_BUDGET_DB','relative/budget.sqlite3')
    with pytest.raises(ValueError):open_budget(tmp_path/'projects')


def test_quote_units_tampering_is_detected_before_another_call(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    run(book)
    with sqlite3.connect(str(book.path)) as db:
        db.execute('UPDATE api_budget_requests SET quote_units=19000000')
    calls = []
    with pytest.raises(ValueError):run(book,operation=lambda:calls.append(True))
    assert calls==[]
