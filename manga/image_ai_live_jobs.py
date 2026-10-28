"""有料依頼の一度限りの送信と、送信を含まない保存復旧。"""
import copy
import fcntl
import hashlib
import json
import shutil
from contextlib import contextmanager
from uuid import UUID,uuid4,uuid5

from manga.budget import BudgetedExecutor
from manga.image_ai_live_plan import prepare,shared_ledger
from manga.image_ai_live_pricing import PROVIDER,actual_charge,bounds,price_quote
from manga.image_ai_live_provider import LiveError,MAX_RESPONSE,response_image,error_info
from manga.image_ai_storage import record
from manga.models import validate_id
from manga.storage import StorageError
from manga.voice_models import digest


@contextmanager
def generation_lock(service):
    service.store.sources_safe()
    path=service.store.root/'.image-ai-live-generation.lock'
    if path.is_symlink(): raise StorageError('画像生成ロックへのリンクを拒否しました。')
    with path.open('a') as stream:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise StorageError('別の画像送信・保存復旧が処理中です。完了後に履歴を確認してください。') from None
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)


def create(service,plan,card,consents,*,approval_token,confirmed,expected_revision,new_request=False,scope_id=None):
    if confirmed is not True or type(new_request) is not bool: raise ValueError('最大費用・個別画像送信・依頼作成を明示承認してください。')
    with service.store.locked():
        data=service.store.checked(expected_revision)
        quoted,_=prepare(service,plan,card,consents)
        if quoted['approval_token']!=approval_token: raise ValueError('見積・参照・版・条件が変わりました。再見積してください。')
        if not quoted['budget']['possible']: raise ValueError('共有予算が不足または請求確認待ちです。新しい画像依頼を作成できません。')
        key=digest(dict(plan=plan,live={k:v for k,v in quoted.items() if k!='budget'}))
        previous=next((j for j in reversed(data['jobs']) if j['job_key']==key),None)
        if previous and not new_request: return copy.deepcopy(previous)
        from manga.gemini_image_contract import MODEL
        if card['model']==MODEL:
            if new_request: raise ValueError('同一画像の無断再生成はできません。対象・指示を変更して別の単発見積を承認するか、手動画像で差し替えてください。')
            service.live_adapter.check_model(card,for_send=True)
            from manga.gemini_image_scope import require_scope
            require_scope(service,plan,card,scope_id)
            quoted['scope_id']=scope_id
        identifier=str(uuid4())
        job=record(request_id=identifier,candidate_id=str(uuid5(UUID(identifier),'candidate')),provider=PROVIDER,state='queued',plan=copy.deepcopy(plan),
                   input=None,job_key=key,api_calls=0,cost_jpy='0',error=None,error_code=None,live=copy.deepcopy(quoted))
        job['id']=identifier
        data['jobs'].append(job); service.store.save(data,'live_job_created',job_id=identifier)
        return copy.deepcopy(job)


def get_job(service,identifier):
    validate_id(identifier)
    job=next((j for j in service.store.load()['jobs'] if j['id']==identifier),None)
    if not job or job['provider']!=PROVIDER: raise ValueError('実APIの依頼IDを選択してください。')
    return job


def update(service,identifier,**values):
    with service.store.locked():
        data=service.store.load(); job=next(j for j in data['jobs'] if j['id']==identifier)
        job.update(values); service.store.save(data,'live_'+job['state'],job_id=identifier)


def response_path(identifier): return f'image_ai/jobs/{identifier}/live_response.json'


def run(service,identifier,*,approved=False):
    if approved is not True: raise ValueError('この1枚の送信を2段階目で確認してください。')
    with generation_lock(service):
        with service.store.locked():
            job=get_job(service,identifier)
            if job['state']=='ready': return next(c for c in service.store.load()['candidates'] if c['id']==job['candidate_id'])
            if job['state']!='queued': raise ValueError('送信済み・中断した依頼は再送しません。履歴から保存だけを復旧してください。')
            if service.live_adapter.fake!=job['live']['fake']: raise ValueError('fakeと本番のアダプターを切り替えて再送しません。')
            from manga.gemini_image_contract import MODEL
            if job['live']['card']['model']==MODEL:
                service.live_adapter.check_model(job['live']['card'],for_send=True)
                from manga.gemini_image_scope import require_scope
                require_scope(service,job['plan'],job['live']['card'],job['live'].get('scope_id'),identifier=identifier)
            quoted,body=prepare(service,job['plan'],job['live']['card'],job['live']['consents'])
            if quoted['approval_token']!=job['live']['approval_token'] or quoted['input_sha256']!=job['live']['input_sha256']: raise ValueError('承認後に入力が変更されています。別依頼として再計画してください。')
            ledger=shared_ledger(service,expected=job['live']['ledger_path'])
            if ledger.request_status(identifier) is not None: raise ValueError('このrequest_idは既に予算台帳にあります。再送は禁止です。')
            if shutil.disk_usage(service.store.root).free<100*1024*1024: raise StorageError('応答・候補保存の空き容量が不足しています。送信せず停止しました。')
            # 書込不能を可能な限り送信前に検出する。ユーザー素材は変更しない。
            service.store.write(f'image_ai/jobs/{identifier}/prepared',job['live']['input_sha256'].encode(),1024)
        card=job['live']['card']
        def operation():
            # 予約・実行権取得の間に別画面で元データが変わっていないか再確認する。
            # 検出時も実行権は使い直さず、予約は手動照合まで保持する。
            from manga.image_ai_planning import current
            with service.store.locked():
                service.store.checked(service.store.load()['revision'])
                current(service.directory,service.project_id,job['plan'],hashed=True)
                shared_ledger(service,expected=job['live']['ledger_path'])
            update(service,identifier,state='reserved')
            update(service,identifier,state='sending',api_calls=1)
            response=service.live_adapter.send(card,body)
            # 応答を保全してから精算。書込失敗時はBudgetedExecutorが請求不明を保持。
            envelope=dict(schema_version=1,request_id=identifier,input_sha256=job['live']['input_sha256'],response=response)
            encoded=json.dumps(envelope,ensure_ascii=False,sort_keys=True).encode()
            service.store.write(response_path(identifier),encoded,MAX_RESPONSE)
            update(service,identifier,state='response_saved',response_sha256=hashlib.sha256(encoded).hexdigest())
            return response
        try:
            BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=identifier,provider=PROVIDER,model=card['model'],kind='image',
                price_quote=price_quote(card,payload_sha256=job['live']['input_sha256']),usage_limits=bounds(card),operation=operation,actual_charge_jpy=lambda response:actual_charge(card,response))
            return publish(service,identifier,ledger)
        except BaseException as error:
            status=ledger.request_status(identifier)
            state='artifact_pending' if status and status['state']=='settled' else 'uncertain' if status else 'not_sent'
            message='応答保存を完了できませんでした。同じ依頼の保存だけを復旧してください。' if state=='artifact_pending' else '送信・請求を確定できません。予約を保持し、自動再送しません。' if state=='uncertain' else '予約前に停止しました。予算・保存状態を確認してください。'
            code,explanation=error_info(error)
            message=explanation+' '+message
            try: update(service,identifier,state='failed' if isinstance(error,LiveError) and status and status['state']=='settled' else state,error=message,error_code=code,cost_jpy=str(status['actual_jpy']) if status and status['actual_jpy'] is not None else '0')
            except (ValueError,OSError,StorageError): pass
            if isinstance(error,(KeyboardInterrupt,SystemExit)): raise
            raise StorageError(message+' ['+code+']') from None


def publish(service,identifier,ledger):
    job=get_job(service,identifier); status=ledger.request_status(identifier)
    if not status or status['state']!='settled' or status['project_id']!=service.project_id or status['provider']!=PROVIDER or status['model']!=job['live']['card']['model']: raise ValueError('請求が確定していません。共有予算画面で明細を照合し、再送せず確認してください。')
    raw=service.store.read(response_path(identifier),MAX_RESPONSE)
    if job.get('response_sha256') and hashlib.sha256(raw).hexdigest()!=job['response_sha256']: raise StorageError('保存済みAPI応答のSHAが異なります。')
    envelope=json.loads(raw)
    if envelope.get('schema_version')!=1 or envelope.get('request_id')!=identifier or envelope.get('input_sha256')!=job['live']['input_sha256']: raise StorageError('保存応答の依頼ID・入力SHAが一致しません。')
    response=envelope['response']; card=job['live']['card']
    # usage不明は人間が0円と照合しただけで画像成功扱いにしない。
    charged=actual_charge(card,response)
    if str(status['actual_jpy'])!=charged:
        from decimal import Decimal
        if Decimal(str(status['actual_jpy']))!=Decimal(charged): raise ValueError('応答usageと台帳の精算額が一致しません。保存を停止します。')
    update(service,identifier,state='settled',cost_jpy=str(status['actual_jpy']),usage=response['usageMetadata'],response_id=response.get('responseId'),returned_model=response.get('modelVersion'))
    usage=response['usageMetadata']
    if usage['promptTokenCount']>card['max_input_tokens'] or usage['candidatesTokenCount']>card['max_output_tokens'] or usage['thoughtsTokenCount']>card['max_thinking_tokens']:
        raise LiveError('usage_bounds_exceeded','凍結した最大利用量を超える応答です。精算を保持し、料金の根拠を再確認してください。')
    from manga.gemini_image_contract import MODEL
    if card['model']==MODEL:
        if usage['candidatesTokenCount']+usage['thoughtsTokenCount']>card['max_output_tokens']:
            raise LiveError('usage_bounds_exceeded','思考を含む出力合計が承認上限を超えました。料金根拠の再確認まで停止します。')
        from manga.gemini_image_contract import response_image as lite_image
        png,width,height=lite_image(card,response)
    else: png,width,height=response_image(card,response)
    receipt=dict(job_id=identifier,candidate_id=job['candidate_id'],sha256=hashlib.sha256(png).hexdigest(),width=width,height=height,
                 path=f"image_ai/candidates/{job['candidate_id']}.png",api_calls=1,cost_jpy=str(status['actual_jpy']))
    service.store.write(receipt['path'],png)
    service.store.write(f'image_ai/jobs/{identifier}/live_receipt.json',json.dumps(receipt,sort_keys=True).encode(),128*1024)
    from manga.image_ai_jobs import publish_candidate
    with service.store.locked():
        service.store.checked(service.store.load()['revision'])
        return publish_candidate(service,identifier,receipt,mock=job['live']['fake'],model=response['modelVersion'])


def recover(service,identifier,*,confirmed=False):
    if confirmed is not True: raise ValueError('APIを再送せず保存だけを復旧することを確認してください。')
    with generation_lock(service):
        job=get_job(service,identifier)
        ledger=shared_ledger(service,expected=job['live']['ledger_path'])
        # アダプター・現行料金の再チェックをしない。凍結した応答と精算だけで復旧。
        return publish(service,identifier,ledger)
