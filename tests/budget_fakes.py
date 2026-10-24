"""Invented test-only price cards; these are never real provider rates."""
from datetime import datetime
from zoneinfo import ZoneInfo

from manga.budget import PriceRates, PriceQuote


def offline_quote(provider='offline', model='fake-model', amount='20', *, now=None):
    day = (now or datetime.now(ZoneInfo('Asia/Tokyo'))).date().isoformat()
    rates = PriceRates(input_per_million_usd='0', output_per_million_usd='0',
        per_item_usd=str(amount), source_url='https://example.com/mock-pricing',
        verified_on=day, provider=provider, model=model,
        item_unit='fake item', tax_note='架空料金・テスト専用（税係数1）')
    return dict(price_quote=PriceQuote(rates,fx_jpy_per_usd='1',safety_multiplier='1'),
        usage_limits=dict(max_input_tokens=0,max_output_tokens=0,max_items=1,retry_limit=0))
