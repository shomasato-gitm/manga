"""画像モデル専用HTTP。認証・リダイレクト拒否・400分類は既存テキスト接続を再利用。"""
import hashlib
import json
import os
import re
import time
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError

from manga.budget.guard import current_execution, claim_provider_call, claim_transport
from manga.gemini_credentials import api_key
from manga.story_ai_provider import NoRedirect
from manga.gemini_image_contract import MODEL, PROVIDER, validate_card, bounds
from manga.image_ai_live_provider import LiveError, MAX_RESPONSE


def enabled():
    return os.environ.get('MANGA_ENABLE_GEMINI_LITE_IMAGE') == '1'


def check(card, *, for_send=False):
    validate_card(card)
    if for_send:
        if not enabled(): raise LiveError('disabled','Gemini Lite画像の有料送信はOFFです。本人が機能フラグと1枚の送信を承認してください。')
        api_key()


def validate_body(card, body):
    try:
        data = json.loads(body)
        config = data['generationConfig']
        if set(data) != {'contents','generationConfig'} or config != dict(candidateCount=1, maxOutputTokens=card['max_output_tokens'],
                responseModalities=['IMAGE'],thinkingConfig=dict(thinkingLevel=card['thinking_level'],includeThoughts=False),
                imageConfig=dict(imageSize='1K',aspectRatio=card['aspect_ratio'])): raise ValueError
        if len(data['contents']) != 1 or set(data['contents'][0]) != {'role','parts'} or data['contents'][0]['role'] != 'user': raise ValueError
        parts = data['contents'][0]['parts']
        if not parts or set(parts[0]) != {'text'} or not isinstance(parts[0]['text'],str) or not parts[0]['text']: raise ValueError
        if len(parts) > card['max_reference_images']+1: raise ValueError
        for part in parts[1:]:
            if set(part) != {'inlineData'} or set(part['inlineData']) != {'mimeType','data'} or part['inlineData']['mimeType'] != 'image/png': raise ValueError
        if len(body) > 16*1024*1024: raise ValueError
    except (ValueError,TypeError,KeyError): raise LiveError('invalid_request','承認した画像本文・上限・参照形式と一致しません。') from None


def require_context(card, body):
    context = current_execution()
    checksum = hashlib.sha256(body).hexdigest()
    if not context or context['provider'] != PROVIDER or context['model'] != MODEL or context['limits'] != bounds(card) or context.get('payload_constraint') != checksum:
        raise LiveError('budget_gate_required','同じ本文に対する予算予約・単発送信権が必要です。')
    validate_body(card,body)
    return checksum


def https(card, body):
    checksum = require_context(card,body)
    check(card,for_send=True)
    claim_transport(PROVIDER,MODEL,checksum)
    request = Request('https://generativelanguage.googleapis.com/v1beta/models/'+MODEL+':generateContent',
        data=body,headers={'Content-Type':'application/json','x-goog-api-key':api_key()},method='POST')
    try:
        with build_opener(NoRedirect()).open(request,timeout=90) as response:
            if getattr(response,'status',None) != 200: raise LiveError('transport_unknown','HTTP応答を確認してください。')
            chunks=[]; size=0; deadline=time.monotonic()+180
            while True:
                if time.monotonic() > deadline: raise TimeoutError
                block=response.read(min(65536,MAX_RESPONSE+1-size))
                if not block: break
                chunks.append(block); size+=len(block)
                if size > MAX_RESPONSE: raise LiveError('response_too_large','応答容量が上限を超えました。')
        return json.loads(b''.join(chunks))
    except HTTPError as error:
        from manga.gemini_http_errors import read_bad_request
        code = read_bad_request(error) if error.code == 400 else 'http_'+str(error.code)
        raise LiveError(code,'画像APIがHTTPエラーを返しました。キー・利用権・モデル・利用枠を確認し、再送せず請求を照合してください。') from None
    except LiveError: raise
    except (TimeoutError,URLError): raise LiveError('transport_unknown','通信が未完了です。予約を保持し、再送せず照合してください。') from None
    except Exception: raise LiveError('incomplete_response','JSON応答を確認できません。自動再送しません。') from None


def send(adapter, card, body):
    checksum = require_context(card,body)
    check(card,for_send=not adapter.fake)
    claim_provider_call(PROVIDER,MODEL,payload_sha256=checksum)
    try:
        response = adapter._transport(MODEL,body) if adapter.fake else https(card,body)
        return sanitize(response)
    except LiveError: raise
    except Exception: raise LiveError('transport_unknown','画像APIの結果を確認できません。予約を保持します。') from None


def sanitize(response):
    if not isinstance(response,dict) or response.get('modelVersion') != MODEL:
        raise LiveError('model_mismatch','応答モデルを照合できません。')
    result = dict(modelVersion=MODEL)
    identifier = response.get('responseId')
    if identifier is not None:
        secret = os.environ.get('GEMINI_API_KEY','')
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',identifier) or secret and secret in identifier:
            raise LiveError('invalid_metadata','応答識別子の形式が不正です。')
        result['responseId'] = identifier
    usage = response.get('usageMetadata')
    if isinstance(usage,dict):
        allowed = {'promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount','toolUsePromptTokenCount','cachedContentTokenCount',
                   'promptTokensDetails','candidatesTokensDetails','cacheTokensDetails','toolUsePromptTokensDetails','serviceTier'}
        if set(usage)-allowed: raise LiveError('usage_unrecognized','未確認のusageがあります。')
        # 自由文・秘密情報をusageとして永続化しない。構造検証は保存より先に行う。
        for key,value in usage.items():
            if key.endswith('Details'):
                if not isinstance(value,list) or len(value)>4 or any(not isinstance(r,dict) or set(r)!={'modality','tokenCount'} or r['modality'] not in ('TEXT','IMAGE') or type(r['tokenCount']) is not int for r in value):
                    raise LiveError('usage_invalid','モダリティusageが不正です。')
            elif key=='serviceTier':
                if value not in ('STANDARD','standard','unspecified','SERVICE_TIER_UNSPECIFIED'): raise LiveError('usage_unrecognized','標準以外の料金tierです。')
            elif type(value) is not int: raise LiveError('usage_invalid','usageが整数ではありません。')
        result['usageMetadata'] = usage
    candidates=response.get('candidates')
    if isinstance(candidates,list) and len(candidates)<=8:
        clean=[]
        for candidate in candidates:
            if not isinstance(candidate,dict): raise LiveError('incomplete_response','画像候補の形式が不正です。')
            reason=candidate.get('finishReason')
            if reason not in ('STOP','MAX_TOKENS','SAFETY','BLOCKLIST','PROHIBITED_CONTENT','IMAGE_SAFETY','RECITATION','OTHER','IMAGE_OTHER','NO_IMAGE','MALFORMED_FUNCTION_CALL','UNEXPECTED_TOOL_CALL'):
                raise LiveError('incomplete_image','画像の完了状態を確認できません。')
            parts=[]
            for part in candidate.get('content',{}).get('parts',[]):
                row={}
                if 'inlineData' in part:
                    value=part['inlineData']
                    if not isinstance(value,dict) or value.get('mimeType') not in ('image/png','image/jpeg','image/webp') or not isinstance(value.get('data'),str): raise LiveError('invalid_image','画像形式が不正です。')
                    row['inlineData']={k:value[k] for k in ('mimeType','data')}
                if part.get('text'): row['unexpectedText']=True
                if part.get('thought'): row['thought']=True
                if part.get('functionCall'): row['functionCall']=True
                parts.append(row)
            clean.append(dict(finishReason=reason,content=dict(parts=parts)))
        result['candidates']=clean
    if response.get('promptFeedback'): result['promptFeedback']=True
    encoded=json.dumps(result)
    secret=os.environ.get('GEMINI_API_KEY','')
    if secret and secret in encoded: raise LiveError('invalid_metadata','応答に秘密情報を含む可能性があるため保存を停止しました。')
    if len(encoded.encode())>MAX_RESPONSE: raise LiveError('response_too_large','画像応答の容量上限です。')
    return result
