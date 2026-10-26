"""公式確認済み設定だけを既存BudgetedExecutorへ渡す。モデル・単価の既定値なし。"""
import re
from decimal import Decimal
from urllib.parse import urlsplit

from manga.budget import PriceRates,PriceQuote

FLAGS=('availability_reviewed','structured_outputs_reviewed','commercial_use_reviewed','maximum_billing_reviewed')


def template():
    return dict(schema_version=1,provider='openai-responses',model='',returned_model_ids=[],verified_on='',
        pricing_url='https://developers.openai.com/api/docs/pricing',model_url='',terms_url='https://openai.com/policies/services-agreement/',
        input_per_million_usd=None,output_per_million_usd=None,max_input_tokens=None,max_output_tokens=16384,reasoning_effort='low',
        fx_jpy_per_usd=None,tax_multiplier=None,safety_multiplier='1.25',tax_note='',**{k:False for k in FLAGS})


def quote(card):
    if isinstance(card,dict) and card.get('provider')=='gemini-text':
        from manga.story_ai_gemini import quote as gemini_quote
        return gemini_quote(card)
    try:
        if not isinstance(card,dict) or set(card)!=set(template()) or type(card['schema_version']) is not int or card['schema_version']!=1 or card['provider']!='openai-responses':raise ValueError
        if any(card[k] is not True for k in FLAGS):raise ValueError('公式のモデル提供・構造化出力・商用条件・最大請求量を確認してください。')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}',card['model']):raise ValueError
        if not isinstance(card['returned_model_ids'],list) or not card['returned_model_ids'] or len(card['returned_model_ids'])>10:raise ValueError
        for model in card['returned_model_ids']:
            if not isinstance(model,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}',model):raise ValueError
        for key in ('pricing_url','model_url','terms_url'):
            url=urlsplit(card[key])
            if url.scheme!='https' or url.hostname not in ('developers.openai.com','platform.openai.com','openai.com') or url.username or url.password or url.query or url.fragment:raise ValueError
        for field,low,high in (('max_input_tokens',1,2000000),('max_output_tokens',256,131072)):
            if type(card[field]) is not int or not low<=card[field]<=high:raise ValueError
        if card['reasoning_effort'] not in ('none','low','medium','high'):raise ValueError
        for field in ('input_per_million_usd','output_per_million_usd'):
            if not Decimal(str(card[field])).is_finite() or not 0<Decimal(str(card[field]))<=1000000:raise ValueError
        rates=PriceRates(card['input_per_million_usd'],card['output_per_million_usd'],source_url=card['pricing_url'],verified_on=card['verified_on'],
            provider='openai-responses',model=card['model'],tax_note=card['tax_note'])
        return PriceQuote(rates,card['fx_jpy_per_usd'],card['safety_multiplier'],card['tax_multiplier'])
    except (KeyError,TypeError,ValueError,ArithmeticError) as error:
        raise ValueError('テキストAPI料金設定が不正・未確認です。モデルの最大入力幅全体と、長文・キャッシュ等の加算を含む上限単価を確認してください。キーをJSONへ含めないでください。') from error


def limits(card):return dict(max_input_tokens=card['max_input_tokens'],max_output_tokens=card['max_output_tokens'],max_items=0,retry_limit=0)


def evidence(card):return quote(card).execution_evidence(card['provider'],card['model'],limits(card))


def charge(card,response):
    if response.get('model') not in card['returned_model_ids']:raise ValueError('応答モデルを確認できません。')
    usage=response.get('usage')
    if not isinstance(usage,dict):raise ValueError('請求対象tokenが不明です。')
    inp,out=usage.get('input_tokens'),usage.get('output_tokens')
    if type(inp) is not int or type(out) is not int or not 0<inp<=10000000 or not 0<=out<=10000000:raise ValueError('請求対象tokenが不正です。')
    q=quote(card)
    # Conservative JPY equivalent, not an invoice or discounted cache price.
    return PriceQuote(q.rates,q.fx_jpy_per_usd,'1',q.tax_multiplier).estimate_jpy(max_input_tokens=inp,max_output_tokens=out)
