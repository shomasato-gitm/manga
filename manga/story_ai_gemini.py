"""Geminiテキスト専用。ツール・画像・キャッシュ・リトライ・モデル切替なし。"""
import json
import time
from decimal import Decimal
from urllib.request import Request,build_opener
from urllib.error import HTTPError, URLError

from manga.budget import PriceQuote,PriceRates
from manga.budget.guard import claim_provider_call,claim_transport,current_execution
from manga.story import load_json_payload
from manga.story_ai_provider import NoRedirect,StoryProviderError
from manga.story_ai_prompts import SYSTEM,prompt
from manga.story_ai_schemas import schema
from manga.voice_models import digest
from manga.gemini_models import LEGACY_MODEL,get_model
from manga.gemini_credentials import api_key

# 旧呼出元・保存形式の互換名。新規UIの既定値や送信先の判定には使わない。
MODEL=LEGACY_MODEL
INPUT_LIMIT=get_model(LEGACY_MODEL).input_limit
FLAGS=('availability_reviewed','structured_outputs_reviewed','commercial_use_reviewed','maximum_billing_reviewed',
       'account_access_reviewed','external_data_reviewed')


def template(model=LEGACY_MODEL):
    profile=get_model(model)
    return dict(schema_version=1,provider='gemini-text',model=profile.id,returned_model_ids=[profile.id],verified_on='',
        pricing_url='https://ai.google.dev/gemini-api/docs/pricing',model_url=profile.model_url,
        terms_url='https://ai.google.dev/gemini-api/terms',input_per_million_usd=profile.input_rate,output_per_million_usd=profile.output_rate,
        max_input_tokens=profile.input_limit,max_output_tokens=16384,**{profile.thinking_field:profile.thinking_value},fx_jpy_per_usd=None,tax_multiplier=None,
        safety_multiplier='1.25',tax_note='',free_tier_reviewed=False,free_tier_verified_on='',**{k:False for k in FLAGS})


def quote(card):
    return _quote(card,diagnostic=False)


def diagnostic_quote(card):
    """利用権限を偽って確認済みにせず、固定文診断だけに使える別の予算契約。"""
    return _quote(card,diagnostic=True)


class GeminiDiagnosticQuote(PriceQuote):
    """同じGemini予算に算入しつつ、固定診断本文以外に転用できない見積。"""
    @property
    def payload_constraint(self):
        from manga.gemini_diagnostics import probe_body
        return digest(probe_body(self.rates.model))


def _quote(card,*,diagnostic):
    try:
        if not isinstance(card,dict):raise ValueError
        profile=get_model(card.get('model'));expected=template(profile.id)
        if set(card)!=set(expected) or type(card['schema_version']) is not int or card['schema_version']!=1:raise ValueError
        if card['provider']!='gemini-text' or card['returned_model_ids']!=[profile.id]:raise ValueError
        if any(type(card[k]) is not bool for k in FLAGS):raise ValueError
        if any(card[k] is not True for k in FLAGS if not diagnostic or k!='account_access_reviewed'):raise ValueError
        if any(card[k]!=expected[k] for k in ('pricing_url','model_url','terms_url','max_input_tokens',profile.thinking_field)):raise ValueError
        if type(card['max_input_tokens']) is not int or type(card[profile.thinking_field]) is not type(profile.thinking_value):raise ValueError
        if type(card['max_output_tokens']) is not int or not 256<=card['max_output_tokens']<=profile.output_limit:raise ValueError
        if type(card['free_tier_reviewed']) is not bool or not isinstance(card['free_tier_verified_on'],str):raise ValueError
        for k,minimum in (('input_per_million_usd',profile.input_rate),('output_per_million_usd',profile.output_rate)):
            value=Decimal(str(card[k]))
            if not value.is_finite() or not Decimal(minimum)<=value<=1000000:raise ValueError
        quote_type=GeminiDiagnosticQuote if diagnostic else PriceQuote
        return quote_type(PriceRates(card['input_per_million_usd'],card['output_per_million_usd'],source_url=card['pricing_url'],
            verified_on=card['verified_on'],provider='gemini-text',model=profile.id,tax_note=card['tax_note']),
            card['fx_jpy_per_usd'],card['safety_multiplier'],card['tax_multiplier'])
    except (TypeError,KeyError,ValueError,ArithmeticError):
        raise ValueError('Geminiの公式モデル・利用権限・送信条件・料金・最大入力幅を確認してください。未確認の接続は送信しません。') from None


def check_mode(card,mode):
    quote(card)
    if mode not in ('paid','verified-free'):raise ValueError('無料確認済みAPIか有料APIを選択してください。')
    if mode=='verified-free':
        check_free_tier(card)


def check_free_tier(card):
    from datetime import datetime,timedelta,timezone,date
    try:age=(datetime.now(timezone(timedelta(hours=9))).date()-date.fromisoformat(card['free_tier_verified_on'])).days
    except (ValueError,TypeError,KeyError):age=99
    if card.get('free_tier_reviewed') is not True or not 0<=age<=1:
        raise ValueError('このアカウントの無料枠・残枠・課金設定・データ利用条件を本日確認してください。有料へ自動切替しません。')


def payload(request):
    card=request['card'];quote(card);profile=get_model(card['model'])
    return dict(systemInstruction=dict(parts=[dict(text=SYSTEM)]),contents=[dict(role='user',parts=[dict(text=prompt(request['kind'],request))])],
        generationConfig=dict(candidateCount=1,maxOutputTokens=card['max_output_tokens'],thinkingConfig=profile.thinking_config,
            **profile.structured_output(schema(request['kind'],(request.get('candidate') or {}).get('id')))))


class GeminiTransportError(StoryProviderError):
    def __init__(self,category):
        self.category=category
        super().__init__('Geminiの通信結果を確認できません。予約を保持し、請求・保存を確認するまで再送しません。')


def transport(body,key):
    context=current_execution()
    provider=context['provider'] if context else 'gemini-text'
    if provider!='gemini-text':raise StoryProviderError('Geminiの予算実行権が必要です。')
    profile=get_model(context['model'] if context else LEGACY_MODEL)
    if context and context.get('payload_constraint') is not None:
        from manga.gemini_diagnostics import probe_body,diagnostic_limits
        if body!=probe_body(profile.id) or context['limits']!=diagnostic_limits(profile.id):raise StoryProviderError('初回診断では固定の診断文と上限だけを送信できます。')
    claim_transport(provider,profile.id,digest(body))
    config=body.get('generationConfig',{})
    allowed={'candidateCount','maxOutputTokens','thinkingConfig'}|({'responseMimeType','responseJsonSchema'} if profile.legacy else {'responseFormat'})
    if set(body)-{'contents','systemInstruction','generationConfig'} or set(config)-allowed:
        raise StoryProviderError('承認したテキスト生成以外の機能・料金tierは送信できません。')
    if (context['limits']['max_input_tokens']!=profile.input_limit or not 256<=context['limits']['max_output_tokens']<=profile.output_limit
            or config.get('maxOutputTokens')!=context['limits']['max_output_tokens']
            or config.get('thinkingConfig')!=profile.thinking_config or config.get('candidateCount')!=1):
        raise StoryProviderError('承認したモデル・思考設定・トークン上限と送信内容が一致しません。')
    req=Request('https://generativelanguage.googleapis.com/v1beta/models/'+profile.id+':generateContent',
        data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode(),headers={'Content-Type':'application/json','x-goog-api-key':key},method='POST')
    from manga.gemini_diagnostic_trace import emit, DiagnosticStorageError
    received=False
    try:
        emit('sending','started')
        with build_opener(NoRedirect()).open(req,timeout=30) as response:
            received=True
            code=getattr(response,'status',None)
            emit('sending','completed')
            emit('http_response','completed',http_status=code if type(code) is int else None)
            deadline=time.monotonic()+120;chunks=[];size=0
            while True:
                if time.monotonic()>deadline:raise TimeoutError
                chunk=response.read1(min(65536,1048577-size))
                if not chunk:break
                chunks.append(chunk);size+=len(chunk)
                if size>1048576:raise ValueError
            emit('response_validation','started')
            return load_json_payload(b''.join(chunks).decode())
    except DiagnosticStorageError:raise
    except HTTPError as error:
        category={400:'bad_request',401:'unauthorized',403:'forbidden',404:'not_found',429:'rate_limited'}.get(error.code,'server_error' if 500<=error.code<=599 else 'http_error')
        if error.code==400:
            from manga.gemini_http_errors import read_bad_request
            category=read_bad_request(error)
        emit('sending','completed')
        emit('http_response','completed',http_status=error.code,reason=category)
        raise GeminiTransportError(category) from None
    except ValueError:raise GeminiTransportError('invalid_response') from None
    except (TimeoutError,URLError) as error:
        timed_out=isinstance(error,TimeoutError) or isinstance(getattr(error,'reason',None),TimeoutError)
        category=('response_timeout' if received else 'connection_timeout') if timed_out else 'network_error'
        raise GeminiTransportError(category) from None
    except Exception:raise GeminiTransportError('network_error') from None


def parse_response(raw,*,model=LEGACY_MODEL):
    profile=get_model(model)
    items=raw.get('candidates',[])
    item=items[0] if len(items)==1 else {};parts=item.get('content',{}).get('parts',[])
    text=''.join(p['text'] for p in parts if isinstance(p.get('text'),str) and not p.get('thought'))
    if len(text.encode())>1048576:raise ValueError
    status='completed' if item.get('finishReason')=='STOP' and all(set(p)<= {'text','thought','thoughtSignature'} for p in parts) else 'incomplete'
    usage=raw.get('usageMetadata',{});inp=usage.get('promptTokenCount');out=usage.get('candidatesTokenCount');thought=usage.get('thoughtsTokenCount',0)
    total=usage.get('totalTokenCount')
    valid=all(type(n) is int and n>=0 for n in (inp,out,thought,total)) and total==inp+out+thought and not usage.get('toolUsePromptTokenCount',0)
    if not profile.legacy and usage.get('serviceTier') not in (None,'standard','unspecified'):
        valid=False  # Standard以外の料金へ自動で切り替えて精算しない。
    model=raw.get('modelVersion') if raw.get('modelVersion')==profile.id else None
    return dict(text=text,status=status,usage=dict(input_tokens=inp,output_tokens=out+thought) if valid else None,model=model,response_id=None)


class GeminiStoryAI:
    def __init__(self,*,send=None,key=None):self.send=send or transport;self.key=key

    def prepare(self,request):
        body=payload(request)
        if len(json.dumps(body,ensure_ascii=False).encode())>262144:raise StoryProviderError('送信する資料が大きすぎます。条件・参考情報を短くしてください。')
        if not self.key:api_key()
        return body

    def generate(self,request):
        body=self.prepare(request);execution=current_execution();profile=get_model(request['card']['model'])
        if not execution or execution['model']!=profile.id or execution['request_id']!=request['id'] or execution['limits']['max_output_tokens']!=request['card']['max_output_tokens'] or execution['limits']['max_input_tokens']!=profile.input_limit:
            raise StoryProviderError('このモデルと入力・出力上限の予算実行権が必要です。')
        claim_provider_call('gemini-text',profile.id,payload_sha256=digest(body))
        try:
            return parse_response(self.send(body,self.key or api_key()),model=profile.id)
        except Exception:raise StoryProviderError('Geminiの結果・利用量が不明です。請求確認まで再送しません。') from None
