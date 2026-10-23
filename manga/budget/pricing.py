"""Fail-closed cost quotation. Rate cards are supplied externally and versioned.

No automatically fetched or stale model prices: an unknown rate MUST reject
the planned request. Rates are USD and represent maximum billable usage.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING


def _amount(value, name):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return amount
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name}は0以上の有限な数値が必要です。") from exc


def _count(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}は0以上の整数にしてください。")
    return value


@dataclass(frozen=True)
class PriceRates:
    """Provider price snapshot; all USD amounts, not unverified model defaults."""
    input_per_million_usd: str
    output_per_million_usd: str
    per_item_usd: str = "0"
    source_url: str = ""
    verified_on: str = ""

    def __post_init__(self):
        for field in ("input_per_million_usd", "output_per_million_usd", "per_item_usd"):
            _amount(getattr(self, field), field)
        if not self.source_url or not self.verified_on:
            raise ValueError("価格の出典URLと確認日を登録してください。")


@dataclass(frozen=True)
class PriceQuote:
    """Conservative max charge based on maximum billable units, not averages."""
    rates: PriceRates
    fx_jpy_per_usd: str = "160"
    safety_multiplier: str = "1.25"

    def estimate_jpy(self, *, max_input_tokens=0, max_output_tokens=0, max_items=0, extra_max_usd="0"):
        inp = _count(max_input_tokens, "入力上限トークン")
        out = _count(max_output_tokens, "出力上限トークン")
        units = _count(max_items, "生成上限件数")
        fx = _amount(self.fx_jpy_per_usd, "想定為替")
        factor = _amount(self.safety_multiplier, "安全係数")
        if fx <= 0 or factor < 1:
            raise ValueError("為替は正数、安全係数は1以上にしてください。")
        usd = (
            _amount(self.rates.input_per_million_usd, "入力単価") * inp / Decimal(1000000)
            + _amount(self.rates.output_per_million_usd, "出力単価") * out / Decimal(1000000)
            + _amount(self.rates.per_item_usd, "件数単価") * units
            + _amount(extra_max_usd, "追加上限")
        )
        total = (usd * fx * factor).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
        if total <= 0:
            raise ValueError("正の最大料金見積もりが必要です。")
        return total
