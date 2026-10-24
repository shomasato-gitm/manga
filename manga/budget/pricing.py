"""Fail-closed cost quotation. Rate cards are supplied externally and versioned.

No automatically fetched or stale model prices: an unknown rate MUST reject
the planned request. Rates are USD and represent maximum billable usage.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
from urllib.parse import urlsplit


def _amount(value, name):
    try:
        raw = str(value)
        if len(raw)>128:
            raise ValueError
        amount = Decimal(raw)
        if not amount.is_finite() or amount < 0 or amount>Decimal('1e12') or (amount and amount.adjusted() < -100):
            raise ValueError
        return amount
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name}は0以上の有限な数値が必要です。") from exc


def _count(value, name):
    if type(value) is not int or not 0<=value<=10**12:
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
    provider: str = ""
    model: str = ""
    tax_note: str = ""
    item_unit: str = ""
    max_age_days: int = 30

    def __post_init__(self):
        for field in ("input_per_million_usd", "output_per_million_usd", "per_item_usd"):
            _amount(getattr(self, field), field)
        if not isinstance(self.source_url,str) or not self.source_url or not isinstance(self.verified_on,str) or not self.verified_on:
            raise ValueError("価格の出典URLと確認日を登録してください。")
        url = urlsplit(self.source_url)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in self.source_url):
            raise ValueError('価格表には認証情報・クエリを含まないHTTPSの公式出典URLを指定してください。')
        try:
            if date.fromisoformat(self.verified_on).isoformat()!=self.verified_on:
                raise ValueError
        except (ValueError,TypeError) as error:
            raise ValueError('料金の確認日はYYYY-MM-DDで指定してください。') from error
        if type(self.max_age_days) is not int or not 1<=self.max_age_days<=30:
            raise ValueError('料金表の確認間隔は1〜30日で指定してください。')


@dataclass(frozen=True)
class PriceQuote:
    """Conservative max charge based on maximum billable units, not averages."""
    rates: PriceRates
    fx_jpy_per_usd: str
    safety_multiplier: str = "1.25"
    tax_multiplier: str = "1"

    def estimate_jpy(self, *, max_input_tokens=0, max_output_tokens=0, max_items=0, extra_max_usd="0"):
        inp = _count(max_input_tokens, "入力上限トークン")
        out = _count(max_output_tokens, "出力上限トークン")
        units = _count(max_items, "生成上限件数")
        fx = _amount(self.fx_jpy_per_usd, "想定為替")
        factor = _amount(self.safety_multiplier, "安全係数")
        tax = _amount(self.tax_multiplier, '税の最大係数')
        if fx <= 0 or factor < 1 or tax < 1:
            raise ValueError("為替は正数、安全係数は1以上にしてください。")
        with localcontext() as context:
            context.prec = 512
            usd = (
                _amount(self.rates.input_per_million_usd, "入力単価") * inp / Decimal(1000000)
                + _amount(self.rates.output_per_million_usd, "出力単価") * out / Decimal(1000000)
                + _amount(self.rates.per_item_usd, "件数単価") * units
                + _amount(extra_max_usd, "追加上限")
            )
            total = (usd * fx * factor * tax).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
        if total <= 0:
            raise ValueError("正の最大料金見積もりが必要です。")
        return total

    def execution_evidence(self, provider, model, limits, *, now=None):
        """Validate a manually verified rate snapshot and explicit single-call bounds."""
        from dataclasses import asdict
        from .ledger import _now_and_month, _yen_to_units, _check_label
        if (_check_label(provider,'プロバイダー')!=provider or _check_label(model,'モデル')!=model
            or provider!=self.rates.provider or model!=self.rates.model
            or not isinstance(self.rates.tax_note,str) or not self.rates.tax_note.strip()):
            raise ValueError('料金表のプロバイダー・モデル・税の適用範囲を明記し、呼び出し先と一致させてください。')
        today = date.fromisoformat(_now_and_month(now)[0][:10])
        age = (today-date.fromisoformat(self.rates.verified_on)).days
        if not 0<=age<=self.rates.max_age_days:
            raise ValueError('料金表が期限切れ、または確認日が未来です。公式料金を確認し直してください。')
        allowed = {'max_input_tokens','max_output_tokens','max_items','extra_max_usd','retry_limit'}
        required = allowed-{'extra_max_usd'}
        if not isinstance(limits,dict) or not required<=set(limits)<=allowed or type(limits.get('retry_limit')) is not int or limits['retry_limit']!=0:
            raise ValueError('最大利用量とretry_limit=0を明示してください。自動再試行は許可しません。')
        bounds = {key:limits.get(key,0) for key in ('max_input_tokens','max_output_tokens','max_items')}
        if bounds['max_items'] and (not isinstance(self.rates.item_unit,str) or not self.rates.item_unit.strip()):
            raise ValueError('件数料金の単位（文字・画像など）を明記してください。')
        bounds['extra_max_usd'] = limits.get('extra_max_usd','0')
        amount = self.estimate_jpy(**bounds)
        _yen_to_units(amount,positive=True)
        return dict(rates=asdict(self.rates), fx_jpy_per_usd=str(self.fx_jpy_per_usd),
                    safety_multiplier=str(self.safety_multiplier),tax_multiplier=str(self.tax_multiplier),
                    bounds={**bounds,'extra_max_usd':str(bounds['extra_max_usd'])},max_attempts=1,
                    computed_max_jpy=str(amount))
