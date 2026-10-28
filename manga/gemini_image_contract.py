"""Gemini 3.1 Flash Lite Image専用契約。旧画像モデルの停止条件は変更しない。"""
import copy
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from manga.budget import PriceQuote, PriceRates
from manga.image_ai_live_provider import LiveError

MODEL = 'gemini-3.1-flash-lite-image'
PROVIDER = 'gemini-live'
PRICING = 'https://ai.google.dev/gemini-api/docs/pricing'
PROTOCOL = 'https://ai.google.dev/api/generate-content'
TERMS = 'https://ai.google.dev/gemini-api/terms'
THINKING = 'https://ai.google.dev/gemini-api/docs/generate-content/thinking'
ASPECTS = {'1:1':(1024,1024), '3:2':(1536,1024), '2:3':(1024,1536)}
FLAGS = ('maximum_charge_verified', 'terms_verified', 'account_access_reviewed', 'external_data_reviewed')


def template():
    return dict(provider=PROVIDER, model=MODEL, returned_models=[MODEL], verified_on='', source_url=PRICING,
        protocol_url=PROTOCOL, terms_url=TERMS, tax_note='', input_per_million_usd='0.25', output_per_million_usd='30',
        text_output_per_million_usd='1.50', fx_jpy_per_usd=None, tax_multiplier=None, safety_multiplier='1.25',
        max_input_tokens=65536, max_output_tokens=2048, max_thinking_tokens=2048,
        max_reference_images=4, max_reference_bytes=2097152, image_size='1K', aspect_ratio='1:1', width=1024, height=1024,
        thinking_level='minimal', **{key:False for key in FLAGS})


def validate_card(raw):
    expected = template()
    try:
        if not isinstance(raw,dict) or set(raw) != set(expected): raise ValueError
        fixed = ('provider','model','returned_models','source_url','protocol_url','terms_url','max_input_tokens',
                 'max_reference_images','max_reference_bytes','image_size')
        if any(raw[k] != expected[k] or type(raw[k]) is not type(expected[k]) for k in fixed): raise ValueError
        if any(raw[k] is not True for k in FLAGS): raise ValueError
        if raw['thinking_level'] not in ('minimal','high'): raise ValueError
        limit = 2048 if raw['thinking_level'] == 'minimal' else 4096
        if type(raw['max_output_tokens']) is not int or raw['max_output_tokens'] != limit or type(raw['max_thinking_tokens']) is not int or raw['max_thinking_tokens'] != limit: raise ValueError
        if raw['aspect_ratio'] not in ASPECTS or (raw['width'],raw['height']) != ASPECTS[raw['aspect_ratio']] or type(raw['width']) is not int or type(raw['height']) is not int: raise ValueError
        for key, minimum in [('input_per_million_usd','0.25'),('output_per_million_usd','30'),('text_output_per_million_usd','1.50')]:
            rate = Decimal(str(raw[key]))
            if not rate.is_finite() or not Decimal(minimum) <= rate <= 1000000: raise ValueError
        if Decimal(str(raw['text_output_per_million_usd']))>Decimal(str(raw['output_per_million_usd'])): raise ValueError
        quote(raw).estimate_jpy(**{k:v for k,v in bounds(raw).items() if k != 'retry_limit'})
        return copy.deepcopy(raw)
    except (ValueError,TypeError,KeyError,ArithmeticError):
        raise ValueError('Gemini Lite画像の公式モデル・上限・有料料金・利用権・外部送信条件を確認してください。未確認なら送信しません。') from None


@dataclass(frozen=True)
class ImageQuote(PriceQuote):
    payload_constraint: str = None


def quote(card, *, settlement=False, payload_sha256=None):
    return ImageQuote(PriceRates(card['input_per_million_usd'],card['output_per_million_usd'],
        source_url=PRICING, verified_on=card['verified_on'], provider=PROVIDER, model=MODEL, tax_note=card['tax_note']),
        card['fx_jpy_per_usd'], '1' if settlement else card['safety_multiplier'], card['tax_multiplier'], payload_sha256)


def bounds(card):
    # maxOutputTokensは画像・テキスト・思考の合計上限。全部を最高の画像単価で予約。
    return dict(max_input_tokens=65536, max_output_tokens=card['max_output_tokens'], max_items=0, retry_limit=0)


def request_body(plan, card, references):
    import base64
    from manga.images import prepare_image
    validate_card(card)
    parts = [dict(text=plan['prompt'] + '\n今回の出力指定を優先：1K、縦横比' + card['aspect_ratio'] + '、画像1枚。文字・吹き出しなし。添付した人物基準画像の髪・瞳・衣装・身体的特徴を維持。')]
    for ref, raw in references:
        if len(raw) > card['max_reference_bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256'] or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise LiveError('reference_changed','参照画像の形式・容量・SHAを照合できません。')
        prepare_image(raw)
        parts.append(dict(inlineData=dict(mimeType='image/png',data=base64.b64encode(raw).decode())))
    if len(references) > card['max_reference_images']: raise LiveError('input_too_large','参照画像は4枚までです。')
    body = dict(contents=[dict(role='user',parts=parts)],generationConfig=dict(candidateCount=1,
        maxOutputTokens=card['max_output_tokens'],responseModalities=['IMAGE'],
        thinkingConfig=dict(thinkingLevel=card['thinking_level'],includeThoughts=False),
        imageConfig=dict(imageSize='1K',aspectRatio=card['aspect_ratio'])))
    raw = json.dumps(body,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    if len(raw) > 16*1024*1024: raise LiveError('input_too_large','依頼本文は16MiB以下にしてください。')
    return raw


def usage(card, response):
    if response.get('modelVersion') != MODEL: raise LiveError('model_mismatch','応答モデルが承認したモデルと異なります。')
    value = response.get('usageMetadata')
    counters = ('promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount')
    allowed = set(counters) | {'cachedContentTokenCount','toolUsePromptTokenCount','promptTokensDetails','candidatesTokensDetails','cacheTokensDetails','toolUsePromptTokensDetails','serviceTier'}
    if not isinstance(value,dict) or set(value)-allowed: raise LiveError('usage_unknown','未確認の課金usageがあります。予約を保持します。')
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 10**9 for k in counters): raise LiveError('usage_invalid','入力・出力・思考・合計usageを確認できません。')
    inp, out, thought, total = (value[k] for k in counters)
    if inp <= 0 or total != inp+out+thought: raise LiveError('usage_inconsistent','課金usageの合計が一致しません。')
    for key in ('cachedContentTokenCount','toolUsePromptTokenCount'):
        if key in value and (type(value[key]) is not int or value[key] != 0): raise LiveError('usage_unrecognized','キャッシュ・ツール課金には対応しません。')
    if value.get('serviceTier','STANDARD') not in ('STANDARD','standard','unspecified','SERVICE_TIER_UNSPECIFIED') or value.get('cacheTokensDetails') or value.get('toolUsePromptTokensDetails'):
        raise LiveError('usage_unrecognized','承認対象外の料金tier・追加usageです。')
    def details(key, count):
        rows = value.get(key)
        if rows is None: return None
        if not isinstance(rows,list): raise LiveError('usage_invalid','モダリティusageが不正です。')
        result = {}
        for row in rows:
            if not isinstance(row,dict) or set(row) != {'modality','tokenCount'} or row['modality'] not in ('TEXT','IMAGE') or row['modality'] in result or type(row['tokenCount']) is not int or row['tokenCount'] < 0:
                raise LiveError('usage_unrecognized','未対応のモダリティ課金です。')
            result[row['modality']] = row['tokenCount']
        if sum(result.values()) != count: raise LiveError('usage_inconsistent','モダリティusageの合計が一致しません。')
        return result
    details('promptTokensDetails',inp)
    output = details('candidatesTokensDetails',out)
    parts = [p for c in response.get('candidates',[]) for p in c.get('content',{}).get('parts',[])]
    if output is None:
        if any('text' in p or p.get('unexpectedText') for p in parts): raise LiveError('usage_unknown','テキストと画像の課金内訳を確認できません。')
        output = {'IMAGE':out}
    if any('inlineData' in p for p in parts) and output.get('IMAGE',0) <= 0: raise LiveError('usage_invalid','画像出力のusageが0です。')
    return inp, output.get('IMAGE',0), output.get('TEXT',0), thought


def actual_charge(card,response):
    inp, image, text, thought = usage(card,response)
    extra = Decimal(card['text_output_per_million_usd']) * (text+thought) / 1000000
    return str(quote(card,settlement=True).estimate_jpy(max_input_tokens=inp,max_output_tokens=image,extra_max_usd=str(extra)))


def response_image(card,response):
    from manga.image_ai_live_provider import response_image as old_image
    # 受信した実寸を保存する。非正方形の1Kはモデルの実寸に幅があるため比率も検証。
    import base64
    from manga.images import prepare_image
    candidates = response.get('candidates',[])
    parts = [p for c in candidates for p in c.get('content',{}).get('parts',[])]
    if any(p.get('unexpectedText') for p in parts): raise LiveError('unexpected_output','画像以外の出力を確認してください。')
    image = next((p.get('inlineData') for p in parts if 'inlineData' in p),{})
    try:
        _, width, height = prepare_image(base64.b64decode(image['data'],validate=True))
    except Exception: raise LiveError('invalid_image','画像を検証できません。') from None
    expected = card['width']/card['height']
    if min(width,height) < 384 or max(width,height) > 2048 or width*height > 2100000 or abs(width/height/expected-1) > .02:
        raise LiveError('wrong_dimensions','1Kの画素数・アスペクト比と一致しません。')
    return old_image(dict(card,width=width,height=height),response)
