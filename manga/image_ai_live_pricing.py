"""確認済み最大量のみを共有予算へ渡す。履歴の読込時は期限を再解釈しない。"""
import copy
import json
import re
from pathlib import Path

from manga.budget import PriceQuote, PriceRates
from manga.voice_models import digest

PROVIDER = 'gemini-live'
REASON = 'Developer APIの最大入力・画像出力・thinking課金量と利用条件が未確認のため、実送信は無効です。'
# 人間が根拠を確認した別PRでのみ更新する。JSON/環境変数では解除しない。
NETWORK_VERIFIED = False


def research():
    return json.loads((Path(__file__).resolve().parents[1]/'examples/image_ai/live_research_20261024.json').read_text(encoding='utf-8'))


def validate_card(raw):
    from manga.gemini_image_contract import MODEL
    if isinstance(raw,dict) and raw.get('model')==MODEL:
        from manga.gemini_image_contract import validate_card as validate_lite
        return validate_lite(raw)
    keys = {'provider','model','returned_models','verified_on','source_url','protocol_url','terms_url','tax_note',
            'input_per_million_usd','output_per_million_usd','fx_jpy_per_usd','tax_multiplier','safety_multiplier',
            'max_input_tokens','max_output_tokens','max_thinking_tokens','max_reference_images','max_reference_bytes',
            'maximum_charge_verified','terms_verified','image_size','aspect_ratio','width','height'}
    if not isinstance(raw,dict) or set(raw)!=keys or raw['provider']!=PROVIDER: raise ValueError('画像API料金カードの項目が不正です。')
    if not isinstance(raw['model'],str) or not re.fullmatch('[a-z0-9][a-z0-9.-]{1,100}',raw['model']): raise ValueError('モデルIDが不正です。')
    if not isinstance(raw['returned_models'],list) or not raw['returned_models'] or len(raw['returned_models'])>10 or any(not isinstance(s,str) or not re.fullmatch('[a-z0-9][a-z0-9.-]{1,100}',s) for s in raw['returned_models']): raise ValueError('応答モデルIDを明示してください。')
    for field in ('maximum_charge_verified','terms_verified'):
        if raw[field] is not True: raise ValueError('最大課金量・利用条件の確認が不足しています。')
    for field in ('max_input_tokens','max_output_tokens','max_thinking_tokens','max_reference_images','max_reference_bytes','width','height'):
        if type(raw[field]) is not int or not 0<=raw[field]<=10**7: raise ValueError('最大利用量は整数で指定してください。')
    if not 0<raw['max_input_tokens']<=1000000 or not 0<raw['max_output_tokens']<=1000000 or raw['max_thinking_tokens']>raw['max_output_tokens'] or not 1<=raw['max_reference_bytes']<=8*1024*1024 or not 0<=raw['max_reference_images']<=8: raise ValueError('画像APIの上限設定が不正です。')
    # 初回接続は1K正方形1枚だけ。自動拡張・quality昇格は行わない。
    if (raw['image_size'],raw['aspect_ratio'],raw['width'],raw['height'])!=('1K','1:1',1024,1024): raise ValueError('このアダプターは1K正方形1枚のみ対応します。')
    for field in ('source_url','protocol_url','terms_url'):
        from urllib.parse import urlsplit
        value=raw[field]; url=urlsplit(value)
        if url.scheme!='https' or url.hostname!='ai.google.dev' or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in value): raise ValueError('Google Developer公式資料の認証情報なしURLが必要です。')
    quote=price_quote(raw)
    quote.estimate_jpy(**bounds(raw,for_estimate=True))
    return copy.deepcopy(raw)


def price_quote(card,*,settlement=False,payload_sha256=None):
    from manga.gemini_image_contract import MODEL
    if card.get('model')==MODEL:
        from manga.gemini_image_contract import quote
        return quote(card,settlement=settlement,payload_sha256=payload_sha256)
    return PriceQuote(PriceRates(input_per_million_usd=card['input_per_million_usd'],output_per_million_usd=card['output_per_million_usd'],
                                source_url=card['source_url'],verified_on=card['verified_on'],provider=PROVIDER,model=card['model'],tax_note=card['tax_note']),
                      card['fx_jpy_per_usd'], '1' if settlement else card['safety_multiplier'],card['tax_multiplier'])


def bounds(card,*,for_estimate=False):
    from manga.gemini_image_contract import MODEL
    if card.get('model')==MODEL:
        from manga.gemini_image_contract import bounds as lite_bounds
        values=lite_bounds(card)
        if for_estimate: values.pop('retry_limit')
        return values
    values=dict(max_input_tokens=card['max_input_tokens'],max_output_tokens=card['max_output_tokens']+card['max_thinking_tokens'],max_items=0)
    if not for_estimate: values['retry_limit']=0
    return values


def quotation(card,*,now=None):
    card=validate_card(card); quote=price_quote(card)
    evidence=quote.execution_evidence(PROVIDER,card['model'],bounds(card),now=now)
    return dict(card=card,evidence=evidence,card_sha256=digest(card),max_jpy=evidence['computed_max_jpy'])


def actual_charge(card,response):
    """全出力（思考含む）を最高出力単価で換算。usage不明を0と推測しない。"""
    from manga.image_ai_live_provider import LiveError
    from manga.gemini_image_contract import MODEL
    if card.get('model')==MODEL:
        from manga.gemini_image_contract import actual_charge as lite_charge
        return lite_charge(card,response)
    usage=response.get('usageMetadata')
    if response.get('modelVersion') not in card['returned_models']: raise LiveError('model_mismatch','応答モデルが未確認です。請求確認まで予約を保持します。')
    if not isinstance(usage,dict): raise LiveError('usage_unknown','usageが不明です。請求確認まで予約を保持します。')
    fields=('promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount')
    if set(usage)-set(fields)-{'toolUsePromptTokenCount','cachedContentTokenCount'}: raise LiveError('usage_unrecognized','未確認のusage項目があります。請求を照合するまで予約を保持します。')
    if any(type(usage.get(k)) is not int or not 0<=usage[k]<=10**9 for k in fields): raise LiveError('usage_invalid','usageの課金量が不正または欠落しています。')
    if usage.get('toolUsePromptTokenCount',0)!=0 or usage.get('cachedContentTokenCount',0)!=0: raise LiveError('usage_unrecognized','未対応の追加課金・キャッシュusageです。')
    inp,out,thought,total=(usage[k] for k in fields)
    if inp==0: raise LiveError('usage_invalid','空でない画像依頼の入力usageが0です。課金量を推測せず確認してください。')
    has_image=any('inlineData' in part for candidate in response.get('candidates',[]) for part in candidate.get('content',{}).get('parts',[]))
    if has_image and out==0: raise LiveError('usage_invalid','画像応答があるのに出力usageが0です。無料と仮定せず確認してください。')
    for key in ('toolUsePromptTokenCount','cachedContentTokenCount'):
        if key in usage and type(usage[key]) is not int: raise LiveError('usage_invalid','追加usageの型が不正です。')
    if not total or total!=inp+out+thought: raise LiveError('usage_inconsistent','usageの合計が一致しません。')
    # 上限超過でも正しい利用量は精算する。ledgerが見積超過を検出し以後を停止。
    return str(price_quote(card,settlement=True).estimate_jpy(max_input_tokens=inp,max_output_tokens=out+thought))
