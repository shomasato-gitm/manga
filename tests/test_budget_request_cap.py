"""一括制作で選んだ低い上限も、共通DBの予約トランザクションで守る。"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4

import pytest

from gemini_image_fixtures import card
from manga.budget import BudgetLedger, BudgetedExecutor, BudgetExceeded
from manga.gemini_image_contract import quote, bounds


def test_competing_requests_cannot_exceed_owner_cap(tmp_path):
    path = tmp_path / 'shared.sqlite3'
    BudgetLedger(path)
    project = str(uuid4())
    barrier, release = Barrier(2), Event()
    entered = []
    def worker():
        ledger = BudgetLedger(path)
        barrier.wait(timeout=5)
        def operation():
            entered.append(1)
            assert release.wait(timeout=10)
            return {}
        try:
            BudgetedExecutor(ledger).run(project_id=project, request_id=str(uuid4()), provider='gemini-live',
                model=card()['model'], kind='image', price_quote=quote(card()), usage_limits=bounds(card()),
                operation=operation, actual_charge_jpy=lambda r:'16.0512', project_cap_jpy='20')
            return 'done'
        except BudgetExceeded:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        done, _ = wait(futures, timeout=8, return_when=FIRST_COMPLETED)
        release.set()
        assert done and next(iter(done)).result() == 'blocked'
        assert sorted(f.result() for f in futures) == ['blocked', 'done']
    assert len(entered) == 1
    assert BudgetLedger(path).summary(project)['project_used_jpy'] == Decimal('16.0512')


def test_per_request_cap_never_raises_common_limits(tmp_path):
    ledger = BudgetLedger(tmp_path / 'shared.sqlite3')
    with pytest.raises(ValueError):
        ledger.reserve(project_id=str(uuid4()), request_id=str(uuid4()), provider='test', model='fake', kind='test', max_jpy='1', project_cap_jpy='501')
