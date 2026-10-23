"""Offline tests of the Phase 1 video API cost guard. No paid API calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from manga.budget import (
    BudgetExceeded, BudgetLedger, BudgetedExecutor,
    PriceQuote, PriceRates, ReservationConflict,
)


def pid():
    return str(uuid4())


def ledger(tmp_path, **kw):
    return BudgetLedger(tmp_path / "runtime" / "budget.sqlite3", **kw)


def reserve(book, project, amount, *, request_id=None, provider="gemini", now=None):
    key = request_id or str(uuid4())
    result = book.reserve(
        project_id=project, request_id=key, provider=provider,
        model="test-model", kind="image", max_jpy=amount, now=now,
    )
    return key, result


def test_exact_500_limit_and_reject_without_record(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    reserve(book, work, "499")
    reserve(book, work, "1")
    with pytest.raises(BudgetExceeded):
        reserve(book, work, "0.000001")
    view = book.summary(work)
    assert view["project_used_jpy"] == Decimal("500")
    assert view["project_remaining_jpy"] == Decimal("0")
    assert view["requests_by_status"] == {"reserved": 2}


def test_cancel_only_unsent_releases_reservation(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    key, _ = reserve(book, work, "400")
    book.cancel_not_sent(key)
    assert book.summary(work)["project_remaining_jpy"] == 500
    with pytest.raises(ReservationConflict):
        book.cancel_not_sent(key)
    reserve(book, work, "500")


def test_timeout_is_uncertain_and_cannot_auto_refund(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    key, _ = reserve(book, work, "490")
    book.mark_uncertain(key)
    book.mark_uncertain(key)  # harmless repeated state update
    with pytest.raises(ReservationConflict):
        book.cancel_not_sent(key)
    with pytest.raises(BudgetExceeded):
        reserve(book, work, "11")
    with pytest.raises(ValueError):
        book.reconcile_no_charge(key)
    book.reconcile_no_charge(key, confirmed_no_charge=True)
    assert book.summary(work)["project_remaining_jpy"] == 500


def test_settle_releases_unused_quote_and_is_idempotent(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    key, _ = reserve(book, work, "100")
    book.settle(key, actual_jpy="23.2")
    book.settle(key, actual_jpy="23.2")
    assert book.summary(work)["project_used_jpy"] == Decimal("23.2")
    with pytest.raises(ReservationConflict):
        book.settle(key, actual_jpy="22")
    reserve(book, work, "476.8")


def test_real_overrun_recorded_and_further_calls_blocked(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    key, _ = reserve(book, work, "400")
    book.settle(key, actual_jpy="501")
    assert book.summary(work)["project_used_jpy"] == Decimal("501")
    assert book.summary(work)["project_remaining_jpy"] == 0
    with pytest.raises(BudgetExceeded):
        reserve(book, work, "1")


def test_duplicate_id_is_not_another_paid_request(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    key, state = reserve(book, work, "50")
    assert state == "reserved"
    assert reserve(book, work, "50", request_id=key)[1] == "already_reserved"
    with pytest.raises(ReservationConflict):
        reserve(book, work, "51", request_id=key)
    book.mark_uncertain(key)
    assert reserve(book, work, "50", request_id=key)[1] == "already_uncertain"


def test_shared_month_cap_for_multiple_projects(tmp_path):
    book = ledger(tmp_path, monthly_limit_jpy="700")
    a, b = pid(), pid()
    reserve(book, a, "450")
    reserve(book, b, "250")
    with pytest.raises(BudgetExceeded):
        reserve(book, pid(), "0.01")


def test_provider_month_cap(tmp_path):
    book = ledger(tmp_path, provider_monthly_limits_jpy={"gemini": "100"})
    a = pid()
    reserve(book, a, "100", provider="gemini")
    with pytest.raises(BudgetExceeded):
        reserve(book, pid(), "1", provider="gemini")
    reserve(book, pid(), "100", provider="openai")


def test_calendar_month_rollover_but_work_limit_is_lifetime(tmp_path):
    book = ledger(tmp_path, monthly_limit_jpy="500")
    jst = ZoneInfo("Asia/Tokyo")
    oct31 = datetime(2026, 10, 31, 23, 55, tzinfo=jst)
    nov1 = oct31 + timedelta(minutes=10)
    reserve(book, pid(), "500", now=oct31)
    with pytest.raises(BudgetExceeded):
        reserve(book, pid(), "1", now=oct31)
    second = pid()
    reserve(book, second, "499", now=nov1)
    assert book.summary(second, now=nov1)["month"] == "2026-11"
    with pytest.raises(BudgetExceeded):
        reserve(book, second, "2", now=nov1)


def test_atomic_reservations_across_workers(tmp_path):
    book = ledger(tmp_path)
    work = pid()
    def attempt(_):
        try:
            reserve(book, work, "30")
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(attempt, range(40)))
    assert sum(outcomes) == 16  # 16 x 30 = 480 <= 500
    assert book.summary(work)["project_used_jpy"] == 480


def test_persistence_across_restart(tmp_path):
    path = tmp_path / "budget.sqlite3"
    work = pid()
    a = BudgetLedger(path)
    key, _ = reserve(a, work, "75")
    b = BudgetLedger(path)
    assert b.summary(work)["project_used_jpy"] == 75
    b.settle(key, actual_jpy="20")
    assert a.summary(work)["project_used_jpy"] == 20


@pytest.mark.parametrize("bad", ["nan", "Infinity", "-0.1", None, "-Infinity"])
def test_invalid_amounts_fail_closed(tmp_path, bad):
    book = ledger(tmp_path)
    with pytest.raises(ValueError):
        reserve(book, pid(), bad)


def test_guarded_operation_once_and_no_second_send(tmp_path):
    book = ledger(tmp_path)
    guard = BudgetedExecutor(book)
    work, key = pid(), str(uuid4())
    calls = []
    def operation():
        calls.append(True)
        return {"usage": 8}
    kw = dict(project_id=work, request_id=key, provider="gemini", model="test",
              kind="tts", quote_jpy="10", operation=operation,
              actual_charge_jpy=lambda response: str(response["usage"]))
    assert guard.run(**kw) == {"usage": 8}
    with pytest.raises(ReservationConflict):
        guard.run(**kw)
    assert len(calls) == 1
    assert book.summary(work)["project_used_jpy"] == 8


def test_guard_keeps_money_on_provider_error(tmp_path):
    book = ledger(tmp_path)
    guard = BudgetedExecutor(book)
    work = pid()
    def fail():
        raise TimeoutError("provider timeout")
    with pytest.raises(TimeoutError):
        guard.run(project_id=work, request_id=str(uuid4()),
                  provider="gemini", model="tts", kind="voice",
                  quote_jpy="200", operation=fail,
                  actual_charge_jpy=lambda result: "1")
    assert book.summary(work)["requests_by_status"] == {"uncertain": 1}
    assert book.summary(work)["project_used_jpy"] == 200


def test_guard_keeps_money_on_missing_usage(tmp_path):
    book = ledger(tmp_path)
    guard = BudgetedExecutor(book)
    work = pid()
    with pytest.raises(ValueError):
        guard.run(project_id=work, request_id=str(uuid4()),
                  provider="gemini", model="image", kind="image",
                  quote_jpy="20", operation=lambda: object(),
                  actual_charge_jpy=lambda result: (_ for _ in ()).throw(ValueError("unknown usage")))
    assert book.summary(work)["requests_by_status"] == {"uncertain": 1}


def test_dry_run_is_offline_and_does_not_reserve(tmp_path):
    book = ledger(tmp_path)
    guard = BudgetedExecutor(book)
    work = pid()
    preview = guard.dry_run(project_id=work, quote_jpy="450")
    assert preview["possible_under_project_and_month"] is True
    assert preview["network_calls"] == 0
    assert book.summary(work)["project_used_jpy"] == 0
    assert guard.dry_run(project_id=work, quote_jpy="501")["possible_under_project_and_month"] is False


def test_prices_require_reference_and_conservative_quote():
    rates = PriceRates(input_per_million_usd="1", output_per_million_usd="2",
                       per_item_usd="0.1", source_url="https://example.com/rates",
                       verified_on="2026-10-24")
    quote = PriceQuote(rates)
    assert quote.estimate_jpy(max_input_tokens=1_000_000,
                              max_output_tokens=500_000, max_items=2) == Decimal("440")
    with pytest.raises(ValueError):
        PriceRates(input_per_million_usd="1", output_per_million_usd="1")
    with pytest.raises(ValueError):
        quote.estimate_jpy()


def test_clock_and_ids_validated(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(ValueError):
        reserve(book, "not-a-uuid", "5")
    with pytest.raises(ValueError):
        reserve(book, pid(), "5", now=datetime(2026, 10, 24))
