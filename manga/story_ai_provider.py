"""Responsesの単一POST境界。SDK再試行・ツール・過去会話・追加APIは使わない。"""
import json
import os
import re
import time
from urllib.request import Request,build_opener,HTTPRedirectHandler

from manga.story import load_json_payload
from manga.story_ai_prompts import SYSTEM,prompt
from manga.story_ai_schemas import schema


class StoryProviderError(ValueError):pass


def api_key():
    value=os.environ.get('OPENAI_API_KEY','')
    if not value or any(c.isspace() for c in value):raise StoryProviderError('OPENAI_API_KEYを実行環境へ設定してください。キーを画面やログへ貼り付けないでください。')
    return value


def payload(request,card):
    return dict(model=card['model'],store=False,stream=False,background=False,truncation='disabled',service_tier='default',tools=[],
        max_output_tokens=card['max_output_tokens'],reasoning=dict(effort=card['reasoning_effort']),metadata=dict(manga_request_id=request['id']),
        input=[dict(role='system',content=[dict(type='input_text',text=SYSTEM)]),dict(role='user',content=[dict(type='input_text',text=prompt(request['kind'],request))])],
        text=dict(format=dict(type='json_schema',name='manga_'+request['kind'],strict=True,
            schema=schema(request['kind'],(request.get('candidate') or {}).get('id')))))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*a,**k):raise StoryProviderError('APIのリダイレクトを拒否しました。')


def transport(body,key):
    request=Request('https://api.openai.com/v1/responses',data=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    try:
        with build_opener(NoRedirect()).open(request,timeout=30) as response:
            deadline=time.monotonic()+120;chunks=[];size=0
            while True:
                if time.monotonic()>deadline:raise ValueError
                chunk=response.read1(min(65536,1048577-size))
                if not chunk:break
                chunks.append(chunk);size+=len(chunk)
                if size>1048576:raise ValueError
            return load_json_payload(b''.join(chunks).decode('utf-8'))
    except Exception:raise StoryProviderError('テキストAPIの応答を安全に確認できません。自動再送せず、請求と保存状態を確認してください。') from None


class ResponsesStoryAI:
    def __init__(self,*,send=None,key=None):self.send=send or transport;self.key=key

    def prepare(self,request):
        card=request['card'];body=payload(request,card)
        # Byte limit is a local input-size safety check, never the billing quote.
        if len(json.dumps(body,ensure_ascii=False).encode())>262144:raise StoryProviderError('入力が大きすぎます。ページ数・参考情報・脚本を整理してください。')
        if not self.key:api_key()
        return body

    def generate(self,request):
        card=request['card'];body=self.prepare(request)
        try:
            raw=self.send(body,self.key or api_key())
            parts=[];status=raw.get('status')
            for output in raw.get('output',[]):
                if output.get('type')=='reasoning':continue
                if output.get('type')!='message':status='unexpected_output';continue
                for content in output.get('content',[]):
                    if content.get('type')=='refusal':status='refused'
                    elif content.get('type')=='output_text' and isinstance(content.get('text'),str):parts.append(content['text'])
            value=''.join(parts)
            if len(value.encode())>1048576:raise ValueError
            model=raw.get('model') if raw.get('model') in card['returned_model_ids'] else None
            identifier=raw.get('id','')
            identifier=identifier if isinstance(identifier,str) and re.fullmatch(r'resp_[A-Za-z0-9_-]{1,180}',identifier) else None
            usage=raw.get('usage')
            usage={k:usage.get(k) for k in ('input_tokens','output_tokens')} if isinstance(usage,dict) else None
            return dict(text=value,status=status if status in ('completed','incomplete','failed','refused') else 'unexpected_output',
                        usage=usage,model=model,response_id=identifier)
        except Exception:raise StoryProviderError('テキストAPIの処理結果が不明です。予約を保持し、明細照合まで再送しません。') from None
