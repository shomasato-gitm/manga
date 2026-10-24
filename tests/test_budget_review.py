"""PR #2 safety regressions. All provider operations are offline fakes."""
import sqlite3
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from manga.budget import BudgetLedger, BudgetedExecutor, BudgetExceeded, ReservationConflict

JST = ZoneInfo('Asia/Tokyo')


def reserve(book, work, amount='20', *, request_id=None, now=None):
    key = request_id or str(uuid4())
    book.reserve(project_id=work, request_id=key, provider='offline',
                 model='fake-model', kind='tts', max_jpy=amount, now=now)
    return key


def test_quote_overrun_blocks_other_work_even_below_500(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    key = reserve(book, str(uuid4()))
    book.settle(key, actual_jpy='21')
    with pytest.raises(BudgetExceeded):
        reserve(book, str(uuid4()), '1')


def test_old_pending_charge_is_carried_into_next_month(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3', monthly_limit_jpy='500')
    reserve(book, str(uuid4()), '500', now=datetime(2026, 10, 31, 23, 59, tzinfo=JST))
    with pytest.raises(BudgetExceeded):
        reserve(book, str(uuid4()), '1', now=datetime(2026, 11, 1, tzinfo=JST))


def test_another_instance_cannot_silently_weaken_shared_policy(tmp_path):
    path = tmp_path/'budget.sqlite3'
    book = BudgetLedger(path)
    reserve(book, str(uuid4()), '500')
    with pytest.raises(ValueError):
        BudgetLedger(path, project_limit_jpy='50000', monthly_limit_jpy='50000')


@pytest.mark.parametrize('mutation', [
    "state='settled', actual_units=NULL",
    "state='settled', actual_units=-1",
    "state='reserved', actual_units=1",
])
def test_inconsistent_sqlite_rows_stop_new_reservations(tmp_path, mutation):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    reserve(book, str(uuid4()), '50')
    with sqlite3.connect(str(book.path)) as db:
        db.execute('UPDATE api_budget_requests SET '+mutation)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        reserve(book, str(uuid4()), '1')


def test_micro_yen_rounding_never_rounds_down_at_cap(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    with pytest.raises(BudgetExceeded):
        reserve(book, str(uuid4()), '500.000000000000000000000000000001')


def test_raw_positive_amount_is_not_verified_pricing(tmp_path):
    book = BudgetLedger(tmp_path/'budget.sqlite3')
    calls = []
    with pytest.raises(ValueError):
        BudgetedExecutor(book).run(project_id=str(uuid4()), request_id=str(uuid4()),
            provider='offline', model='fake-model', kind='tts', quote_jpy='20',
            operation=lambda: calls.append(True), actual_charge_jpy=lambda _: '1')
    assert calls == []
