"""An explicit guarded execution boundary for future paid API adapters.

Adapters must never call provider SDKs on their own. Reserve the worst-case
quote before making a request, never silently retry or upgrade models, and
preserve the full reservation on any ambiguous provider/usage outcome.
"""
from .ledger import BudgetLedger, ReservationConflict


class BudgetedExecutor:
    def __init__(self, ledger: BudgetLedger):
        self.ledger = ledger

    def dry_run(self, *, project_id, quote_jpy, now=None):
        """Offline preview; no provider operation or reservation takes place."""
        from .ledger import _yen_to_units
        quote = _yen_to_units(quote_jpy, positive=True)
        summary = self.ledger.summary(project_id, now=now)
        from decimal import Decimal
        price = Decimal(quote) / Decimal(1000000)
        return {
            "possible_under_project_and_month": (
                price <= summary["project_remaining_jpy"]
                and price <= summary["monthly_remaining_jpy"]
            ),
            "quoted_max_jpy": price,
            "reserved_jpy": Decimal("0"),
            "network_calls": 0,
        }

    def run(
        self, *, project_id, request_id, provider, model, kind, quote_jpy,
        operation, actual_charge_jpy, now=None,
    ):
        """Operation is a *single* provider call. Do not auto-repeat failures.

        actual_charge_jpy(response) must return the charge derived from
        provider usage. Unknown usage raises and keeps funds reserved.
        """
        if not callable(operation) or not callable(actual_charge_jpy):
            raise TypeError("API呼び出しと利用額計算の両方を指定してください。")
        state = self.ledger.reserve(
            project_id=project_id, request_id=request_id, provider=provider,
            model=model, kind=kind, max_jpy=quote_jpy, now=now,
        )
        if state != "reserved":
            raise ReservationConflict("同じリクエストIDの再送はできません。")
        try:
            response = operation()
            charged = actual_charge_jpy(response)
        except BaseException:
            self.ledger.mark_uncertain(request_id)
            raise
        try:
            self.ledger.settle(request_id, actual_jpy=charged)
        except BaseException:
            # Provider has already replied: never release its charge reservation.
            self.ledger.mark_uncertain(request_id)
            raise
        return response
