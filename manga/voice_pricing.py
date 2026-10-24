"""手動確認した公式料金表だけを既存予算ゲートへ渡す。単価の既定値なし。"""
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import urlsplit

from manga.budget import PriceRates, PriceQuote


def pricing_template():
    return dict(schema_version=1,provider='aws-polly',verified_on='',
        pricing_url='https://aws.amazon.com/polly/pricing/',api_url='https://docs.aws.amazon.com/polly/latest/dg/API_SynthesizeSpeech.html',
        terms_url='https://aws.amazon.com/service-terms/',commercial_use_reviewed=False,
        japanese_reviewed=False,billing_maximum_reviewed=False,
        engines={engine:dict(per_million_characters_usd=None,fx_jpy_per_usd=None,tax_multiplier=None,
                            safety_multiplier='1.25',tax_note='') for engine in ('standard','neural')})


def price_quote(card,engine):
    if not isinstance(card,dict) or type(card.get('schema_version')) is not int or card.get('schema_version')!=1 or card.get('provider')!='aws-polly':
        raise ValueError('Pollyの公式料金設定を取り込んでください。')
    if set(card)!=set(pricing_template()):raise ValueError('料金JSONに未対応の項目があります。認証情報はJSONへ含めないでください。')
    for key in ('commercial_use_reviewed','japanese_reviewed','billing_maximum_reviewed'):
        if card.get(key) is not True:raise ValueError('商用利用条件・日本語・最大請求量の公式確認が必要です。')
    for key in ('pricing_url','api_url','terms_url'):
        parsed=urlsplit(card.get(key,''))
        if parsed.scheme!='https' or parsed.hostname not in ('aws.amazon.com','docs.aws.amazon.com','pricing.us-east-1.amazonaws.com') or parsed.username or parsed.password or parsed.query:
            raise ValueError('料金・API・利用条件には認証情報を含まない公式URLが必要です。')
    try:
        day=date.fromisoformat(card['verified_on'])
        if day.isoformat()!=card['verified_on']:raise ValueError
        row=card['engines'][engine]
        if set(row)!=set(pricing_template()['engines']['neural']):raise ValueError
        raw=str(row['per_million_characters_usd'])
        if len(raw)>128:raise ValueError
        per_million=Decimal(raw)
        if not per_million.is_finite() or not 0<per_million<=1000000:raise ValueError
        with localcontext() as context:
            context.prec=max(64,len(per_million.as_tuple().digits)+8)
            rate=format(per_million/Decimal(1000000),'f')
        rates=PriceRates('0','0',per_item_usd=rate,source_url=card['pricing_url'],verified_on=card['verified_on'],
                        provider='aws-polly',model=engine,tax_note=row['tax_note'],item_unit='billable character',max_age_days=30)
        return PriceQuote(rates,fx_jpy_per_usd=row['fx_jpy_per_usd'],safety_multiplier=row['safety_multiplier'],tax_multiplier=row['tax_multiplier'])
    except (KeyError,TypeError,InvalidOperation,ValueError) as error:
        raise ValueError('料金単価・確認日・為替・税・engineを確認してください。未確認の単価は利用できません。') from error


def usage_limits(ssml):
    count=len(ssml.encode('utf-8'))
    if not 0<count<=4000:raise ValueError('SSMLが4000バイトを超えます。発話を分割してください。')
    # UTF-8 bytes including tags conservatively exceed billable text characters.
    return dict(max_input_tokens=0,max_output_tokens=0,max_items=count,retry_limit=0)


def converted_charge(quote,characters):
    if type(characters) is not int or not 1<=characters<=1000000:
        raise ValueError('提供元の請求対象文字数を確認できません。')
    # A budget JPY equivalent, not a claim to have retrieved the final invoice.
    actual=PriceQuote(quote.rates,fx_jpy_per_usd=quote.fx_jpy_per_usd,
                      safety_multiplier='1',tax_multiplier=quote.tax_multiplier)
    return actual.estimate_jpy(max_items=characters)
