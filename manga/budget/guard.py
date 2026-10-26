"""An explicit guarded execution boundary for future paid API adapters.

Adapters must never call provider SDKs on their own. Reserve the worst-case
quote before making a request, never silently retry or upgrade models, and
preserve the full reservation on any ambiguous provider/usage outcome.
"""
from .ledger import BudgetLedger, ReservationConflict, _yen_to_units, _units_to_yen
from .pricing import PriceQuote
from contextvars import ContextVar
from threading import Lock

_execution = ContextVar('manga_budget_execution',default=None)
_provider_call_lock = Lock()


def current_execution():
    """送信アダプターの迂回防止用。永続claim取得後のoperation内だけ有効。"""
    value=_execution.get()
    return dict(value) if value is not None else None


def claim_provider_call(provider,model,*,payload_sha256=None):
    """対応アダプターの1リクエスト制限。コピーした実行contextでも共有する。"""
    with _provider_call_lock:
        value=_execution.get()
        if value is None or value['provider']!=provider or value['model']!=model:
            raise ReservationConflict('予算の実行権と送信先が一致しません。')
        if value.get('provider_call_claimed'):
            raise ReservationConflict('この実行権では既に1リクエストを送信しています。再送しません。')
        value['provider_call_claimed']=True
        value['payload_sha256']=payload_sha256


def claim_transport(provider,model,payload_sha256):
    """アダプター検証済みの同じ本文だけをHTTPへ一度渡す。"""
    with _provider_call_lock:
        value=_execution.get()
        if (value is None or value['provider']!=provider or value['model']!=model
            or not value.get('provider_call_claimed') or value.get('payload_sha256')!=payload_sha256
            or value.get('transport_claimed')):
            raise ReservationConflict('承認済みアダプターの単発送信権がありません。直接送信・再送を拒否しました。')
        value['transport_claimed']=True


class BudgetedExecutor:
    def __init__(self, ledger: BudgetLedger):
        self.ledger = ledger

    def dry_run(self, *, project_id, quote_jpy, provider=None, now=None):
        """Offline preview; no provider operation or reservation takes place."""
        from .ledger import _yen_to_units
        quote = _yen_to_units(quote_jpy, positive=True)
        summary = self.ledger.summary(project_id, now=now)
        from decimal import Decimal
        price = Decimal(quote) / Decimal(1000000)
        possible = not summary['blocked'] and (
                price <= summary["project_remaining_jpy"]
                and price <= summary["monthly_remaining_jpy"]
            )
        provider_info = summary['providers'].get(provider)
        possible_provider = provider_info is None or provider_info['used_jpy']+price<=provider_info['limit_jpy']
        return {
            "possible_under_project_and_month": possible,
            "possible": possible and possible_provider,
            "provider_checked": provider is not None,
            "quoted_max_jpy": price,
            "reserved_jpy": Decimal("0"),
            "network_calls": 0,
        }

    def run(
        self, *, project_id, request_id, provider, model, kind, quote_jpy=None,
        operation, actual_charge_jpy, price_quote=None, usage_limits=None, now=None,
    ):
        """Operation is a *single* provider call. Do not auto-repeat failures.

        actual_charge_jpy(response) must return the charge derived from
        provider usage. Unknown usage raises and keeps funds reserved.
        """
        if not callable(operation) or not callable(actual_charge_jpy):
            raise TypeError("API呼び出しと利用額計算の両方を指定してください。")
        if not isinstance(price_quote, PriceQuote):
            raise ValueError('金額だけでは実行できません。確認済みのPriceQuoteと最大利用量が必要です。')
        evidence = price_quote.execution_evidence(provider,model,usage_limits,now=now)
        computed = _yen_to_units(evidence['computed_max_jpy'],positive=True)
        quote = computed if quote_jpy is None else _yen_to_units(quote_jpy,positive=True)
        if quote<computed:
            raise ValueError('事前予約額が料金表と最大利用量から求めた見積を下回っています。')
        evidence['reserved_max_jpy'] = str(_units_to_yen(quote))
        state = self.ledger.reserve(
            project_id=project_id, request_id=request_id, provider=provider,
            model=model, kind=kind, max_jpy=_units_to_yen(quote), now=now, evidence=evidence,
        )
        if state != "reserved":
            raise ReservationConflict("同じリクエストIDの再送はできません。")
        claim = self.ledger.claim_execution(request_id,now=now)
        try:
            token=_execution.set(dict(project_id=project_id,request_id=request_id,provider=provider,model=model,limits=dict(usage_limits)))
            try:
                response = operation()
            finally:
                _execution.reset(token)
            charged = actual_charge_jpy(response)
        except BaseException:
            self.ledger.mark_uncertain(request_id)
            raise
        try:
            self.ledger.settle(request_id, actual_jpy=charged, execution_claim=claim)
        except BaseException:
            # Provider has already replied: never release its charge reservation.
            self.ledger.mark_uncertain(request_id)
            raise
        return response
