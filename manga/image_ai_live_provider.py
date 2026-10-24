"""Googleの単発画像アダプター。本番HTTPは根拠未確認の間は二重に遮断する。"""
import base64
import hashlib
import json
import os
import re

from manga.image_ai_live_pricing import REASON
from manga.images import prepare_image

MAX_RESPONSE = 30*1024*1024
ERRORS = {
    'transport_unknown':'通信が未完了です。', 'incomplete_response':'JSON応答が不完全です。',
    'response_too_large':'応答の容量上限を超えています。', 'invalid_metadata':'応答識別情報が不正です。',
    'no_image':'応答に画像がありません。', 'multiple_images':'複数画像の応答を拒否しました。',
    'invalid_base64':'画像のbase64形式が不正です。', 'invalid_image':'画像が破損・容量超過または未対応形式です。',
    'wrong_dimensions':'画像の寸法が計画と異なります。', 'safety_refusal':'プロバイダーが安全上の理由で拒否しました。',
    'incomplete_image':'画像応答が完了していません。', 'unexpected_output':'未対応のツール・思考出力です。',
    'usage_unknown':'課金usageが不明です。', 'usage_invalid':'usageが欠損または不正です。',
    'usage_unrecognized':'未確認の課金usageがあります。', 'usage_inconsistent':'usageの合計が一致しません。',
    'model_mismatch':'応答モデルが承認したモデルと異なります。', 'usage_bounds_exceeded':'最大利用量を超える応答です。',
}


def error_info(error):
    code=error.code if isinstance(error,LiveError) else 'billing_or_storage_unknown'
    if not isinstance(code,str): code='billing_or_storage_unknown'
    http=re.fullmatch(r'http_?([1-5][0-9]{2})',code)
    if http:
        status=http[1]; return 'http_'+status,'画像APIがHTTP '+status+'を返しました。'
    return (code,ERRORS[code]) if code in ERRORS else ('billing_or_storage_unknown','請求・保存内容を確認してください。')


def _require_budget(model):
    from manga.budget.guard import current_execution
    context=current_execution()
    if context is None or context['provider']!='gemini-live' or context['model']!=model:
        raise LiveError('budget_gate_required','BudgetedExecutorの予約・実行権取得後だけ送信できます。')


class LiveError(ValueError):
    def __init__(self,code,message):
        self.code=code
        super().__init__(message)


def network_allowed():
    # 設定カードを書き換えても未検証の能力を有効化できない。
    from manga.image_ai_live_pricing import NETWORK_VERIFIED,research
    manifest=research()
    if NETWORK_VERIFIED is not True or manifest['network_enabled'] is not True or manifest['maximum_charge_verified'] is not True: raise LiveError('unverified_maximum',REASON)
    if os.environ.get('MANGA_ENABLE_IMAGE_API')!='1': raise LiveError('disabled','実API機能フラグがOFFです。')


def _https(model,body):
    _require_budget(model)
    network_allowed()  # fakeの注入やUIフラグでは本番転送を解除できない。
    key=os.environ.get('GEMINI_API_KEY','')
    if not key.strip(): raise LiveError('authentication','GEMINI_API_KEYを環境変数に設定してください。')
    import urllib.request
    import urllib.error
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**k): return None
    request=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',
                                   data=body,headers={'Content-Type':'application/json','x-goog-api-key':key},method='POST')
    try:
        # 代理URL・リダイレクト・SDK内部のリトライを許可しない。
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(request,timeout=90) as response:
            raw=response.read(MAX_RESPONSE+1)
        if len(raw)>MAX_RESPONSE: raise LiveError('response_too_large','API応答が容量上限を超えています。')
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        code=error.code
        raise LiveError('http_'+str(code),'画像APIがHTTP '+str(code)+'を返しました。再送せず請求を確認してください。') from None
    except LiveError: raise
    except Exception: raise LiveError('transport_unknown','通信・応答が未完了です。自動再送せず請求を確認してください。') from None


class GeminiImageAdapter:
    def __init__(self,*,fake_transport=None):
        if fake_transport is not None and not callable(fake_transport): raise ValueError('fake transportは関数で指定してください。')
        self.fake=fake_transport is not None
        self._transport=fake_transport or _https

    def check(self):
        if not self.fake:
            network_allowed()
            if not os.environ.get('GEMINI_API_KEY','').strip(): raise LiveError('authentication','GEMINI_API_KEYを環境変数に設定してください。')

    def send(self,card,body):
        _require_budget(card['model'])
        self.check()
        try:
            response=self._transport(card['model'],body)
            # 任意オブジェクトや無制限応答を保存しない。HTTPヘッダー・例外本文・キーを除外。
            return sanitize(response)
        except LiveError: raise
        except Exception: raise LiveError('transport_unknown','API応答を確認できません。予約を保持し、再送を禁止します。') from None


def sanitize(response):
    if not isinstance(response,dict): raise LiveError('incomplete_response','画像APIのJSON応答が不完全です。')
    result={k:response[k] for k in ('modelVersion','responseId','usageMetadata','candidates') if k in response}
    if response.get('promptFeedback'): result['promptFeedback']=True
    # 内容の詳細は候補化時に検証する。任意の診断/headers/errorを落とす。
    for key in ('modelVersion','responseId'):
        if key in result and (not isinstance(result[key],str) or len(result[key])>200): raise LiveError('invalid_metadata','API識別情報が不正です。')
    if 'usageMetadata' in result and isinstance(result['usageMetadata'],dict):
        allowed={'promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount','toolUsePromptTokenCount','cachedContentTokenCount'}
        usage=result['usageMetadata']
        result['usageMetadata']={k:v for k,v in usage.items() if k in allowed}
        if set(usage)-allowed: result['usageMetadata']['unsupportedUsage']=True
    candidates=result.get('candidates')
    if isinstance(candidates,list):
        clean=[]
        for candidate in candidates:
            if not isinstance(candidate,dict): raise LiveError('incomplete_response','画像候補の構造が不正です。')
            parts=[]
            for part in candidate.get('content',{}).get('parts',[]):
                if not isinstance(part,dict): raise LiveError('incomplete_response','画像部品の構造が不正です。')
                row={}
                if 'inlineData' in part:
                    value=part['inlineData']
                    row['inlineData']={k:value[k] for k in ('mimeType','data') if k in value} if isinstance(value,dict) else None
                if part.get('thought'): row['thought']=True
                if part.get('functionCall'): row['functionCall']=True
                parts.append(row)
            clean.append(dict(finishReason=candidate.get('finishReason'),content=dict(parts=parts)))
        result['candidates']=clean
    if len(json.dumps(result,ensure_ascii=False).encode())>MAX_RESPONSE: raise LiveError('response_too_large','API応答が容量上限を超えています。')
    return result


def request_body(plan,card,references):
    parts=[dict(text=plan['prompt']+'\n今回実際に添付する参照画像ID：'+', '.join(r['id'] for r,_ in references)+'\n添付画像だけを参照。出力は画像1枚、文字・吹き出しなし。')]
    for ref,raw in references:
        if len(raw)>card['max_reference_bytes'] or hashlib.sha256(raw).hexdigest()!=ref['sha256']: raise LiveError('reference_changed','参照画像の容量・SHAが一致しません。')
        normalized,w,h=prepare_image(raw)
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'): raise LiveError('reference_format','参照画像は検証済みPNGにしてください。')
        parts.append(dict(inlineData=dict(mimeType='image/png',data=base64.b64encode(raw).decode('ascii'))))
    body=dict(contents=[dict(role='user',parts=parts)],generationConfig=dict(candidateCount=1,responseModalities=['IMAGE'],
              maxOutputTokens=card['max_output_tokens'],thinkingConfig=dict(thinkingBudget=card['max_thinking_tokens']),
              imageConfig=dict(imageSize=card['image_size'],aspectRatio=card['aspect_ratio'])))
    raw=json.dumps(body,ensure_ascii=False,sort_keys=True).encode()
    if len(raw)>16*1024*1024: raise LiveError('input_too_large','送信本文が16MiBを超えています。')
    return raw


def response_image(card,response):
    candidates=response.get('candidates')
    if response.get('promptFeedback'): raise LiveError('safety_refusal','安全上の理由で画像生成が拒否されました。')
    if not isinstance(candidates,list) or not candidates: raise LiveError('no_image','画像0件です。請求済みでも再送しません。')
    if len(candidates)!=1: raise LiveError('multiple_images','1枚を超える応答候補を拒否しました。')
    row=candidates[0]
    if row.get('finishReason') in ('SAFETY','BLOCKLIST','PROHIBITED_CONTENT','IMAGE_SAFETY','RECITATION'): raise LiveError('safety_refusal','安全上の理由で画像生成が拒否されました。')
    if row.get('finishReason')!='STOP': raise LiveError('incomplete_image','画像応答が完了していません。')
    parts=row.get('content',{}).get('parts',[])
    if any(p.get('functionCall') or p.get('thought') for p in parts): raise LiveError('unexpected_output','未対応のツール・思考画像を拒否しました。')
    images=[p['inlineData'] for p in parts if 'inlineData' in p]
    if len(images)!=1: raise LiveError('no_image' if not images else 'multiple_images','画像はちょうど1枚必要です。')
    image=images[0]
    if not isinstance(image,dict) or image.get('mimeType') not in ('image/png','image/jpeg','image/webp') or not isinstance(image.get('data'),str): raise LiveError('invalid_image','API画像形式が不正です。')
    try:
        raw=base64.b64decode(image['data'],validate=True)
    except (ValueError,TypeError): raise LiveError('invalid_base64','画像のbase64形式が不正です。') from None
    try:
        png,width,height=prepare_image(raw)
    except Exception: raise LiveError('invalid_image','画像が破損・容量超過、またはbase64が不正です。') from None
    if (width,height)!=(card['width'],card['height']): raise LiveError('wrong_dimensions','返却画像が凍結した1K寸法と異なります。')
    return png,width,height
