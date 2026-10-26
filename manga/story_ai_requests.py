"""1リクエストの実行・応答保全・精算。外部送信は共通予算ゲート内だけ。"""
import copy
import hashlib
import json
import shutil
from decimal import Decimal
from uuid import uuid4

from manga.budget import BudgetedExecutor
from manga.budget.config import open_budget
from manga.models import now_iso
from manga.storage import StorageError
from manga.story_ai_pricing import quote,limits,charge,evidence
from manga.story_ai_mock import MockStoryAI
from manga.story_ai_provider import ResponsesStoryAI
from manga.story_ai_results import candidate,evaluation,apply


class StopStory(ValueError):
    def __init__(self,reason,message):super().__init__(message);self.reason=reason


def next_request(service,job_id):
    with service.store.source_lock(),service.store.lock():
        data=service.store.load();job=next(j for j in data['jobs'] if j['id']==job_id)
        if service.source(job['settings'])[1]!=job['source_token']:raise StopStory('stale_sources','作品・人物・脚本・参考情報が変わりました。残りのAPIを送信せず停止しました。')
        if job['provider']!='mock' and (not job.get('approval') or job['approval'].get('phase')!=job['phase']):
            raise StopStory('approval_required','次の1送信の承認が必要です。以前の一括承認は使用しません。')
        kind={'evaluate':'evaluation','reevaluate':'evaluation'}.get(job['phase'],job['phase'])
        if kind=='improve' and job['cycle']>=min(5,job['settings']['max_cycles']):raise StopStory('improvement_limit','改善上限です。追加APIは送信しません。')
        cycle=job['cycle']+(1 if kind=='improve' else 0)
        existing=next((r for r in data['requests'] if r['job_id']==job_id and r['phase']==job['phase'] and r['cycle']==cycle),None)
        if existing:
            if job['provider']!='mock' and existing['state'] in ('queued','not_sent') and job['approval'].get('request_id') is None:
                job['approval']['request_id']=existing['id'];service.store.save(data,'request_reapproved',request_id=existing['id'])
            return copy.deepcopy(existing)
        if shutil.disk_usage(service.store.root).free<32*1024*1024:raise StopStory('storage_failure','AI応答保存の空き容量が不足しています。未送信で停止しました。')
        identifier=str(uuid4())
        request=dict(id=identifier,request_id=identifier,result_id=str(uuid4()),job_id=job_id,state='queued',created_at=now_iso(),
            kind=kind,phase=job['phase'],cycle=cycle,settings=copy.deepcopy(job['settings']),context=copy.deepcopy(job['context']),card=copy.deepcopy(job['card']),
            candidate=None,evaluation=None,concept=None,receipt_sha256=None)
        if kind=='draft':request['concept']=service.concept(data,job['selected_concept_id'])[0]
        if kind in ('improve','evaluation'):
            request['candidate']=copy.deepcopy(candidate(data,job['current_candidate_id'] if kind=='evaluation' else job['best_candidate_id']))
        if kind=='improve':request['evaluation']=copy.deepcopy(evaluation(data,request['candidate']['id']))
        if job['provider']!='mock':job['approval']['request_id']=identifier
        data['requests'].append(request);service.store.save(data,'request_created',request_id=identifier)
        return copy.deepcopy(request)


def set_request(service,identifier,**fields):
    with service.store.lock():
        data=service.store.load();request=next(r for r in data['requests'] if r['id']==identifier)
        request.update(fields);service.store.save(data,'request_state',request_id=identifier,state=request['state'])


def ledger_for(service,job):
    ledger=open_budget(service.directory)
    if str(ledger.path.resolve())!=job['ledger_path']:raise StopStory('ledger_mismatch','予算台帳の保存先が生成時と異なります。0円から再開せず元の台帳を確認してください。')
    return ledger


def enforce_job_budget(service,job,ledger):
    used=Decimal('0')
    for request in service.store.load()['requests']:
        if request['job_id']!=job['id']:continue
        status=ledger.request_status(request['id'])
        if status and status['state']=='settled':used+=status['actual_jpy']
        elif status:raise StopStory('provider_uncertain','この依頼に請求未確定のリクエストがあります。自動再送しません。')
    per=Decimal(evidence(job['card'])['computed_max_jpy'])
    if used+per>Decimal(job['spend_cap_jpy']):raise StopStory('budget_limit','次の最大見積が依頼単位の残額を超えます。未送信で停止しました。')
    if not BudgetedExecutor(ledger).dry_run(project_id=service.project_id,quote_jpy=per,provider=job['provider'])['possible']:
        raise StopStory('budget_limit','作品・全作品月間の残額不足、または請求照合待ちです。未送信で停止しました。')


def receipt(service,request):
    content=service.store.read(f"story_ai/responses/{request['id']}.json")
    if request['receipt_sha256'] and hashlib.sha256(content).hexdigest()!=request['receipt_sha256']:raise StopStory('storage_failure','保存済みAI応答が変更されています。')
    value=json.loads(content)
    if value['request_id']!=request['id']:raise StopStory('storage_failure','保存済み応答のIDが一致しません。')
    return value['response'],hashlib.sha256(content).hexdigest()


def execute(service,job,request):
    if request['state'] not in ('queued','not_sent'):raise StopStory('provider_uncertain','同じリクエストを再送しません。保存・請求の復旧を先に確認してください。')
    paid=job['provider']!='mock';ledger=ledger_for(service,job) if paid else None
    if paid:
        job=next(j for j in service.store.load()['jobs'] if j['id']==job['id'])
        if not service.api_enabled(job.get('access_mode','paid')):raise StopStory('paid_disabled','API機能は無効です。APIを送信しません。')
        if job.get('approval',{}).get('request_id')!=request['id'] or job['approval'].get('phase')!=request['phase']:
            raise StopStory('approval_required','今回の送信権がありません。個別承認してください。')
        if job['provider']=='gemini-text':
            from manga.story_ai_gemini import check_mode
            check_mode(job['card'],job.get('access_mode','paid'))
        enforce_job_budget(service,job,ledger)
    from manga.story_ai_gemini import GeminiStoryAI
    adapter=service.adapter or (GeminiStoryAI() if job['provider']=='gemini-text' else ResponsesStoryAI() if paid else MockStoryAI())
    if paid and isinstance(adapter,(ResponsesStoryAI,GeminiStoryAI)):
        try:adapter.prepare(request)
        except ValueError as error:raise StopStory('input_invalid',str(error)) from None
    # Probe the same immutable writer before risking a paid call.
    service.store.write(f"story_ai/jobs/{job['id']}/storage-probe-{request['id']}",b'probe')
    set_request(service,request['id'],state='sending')
    def operation():
        response=adapter.generate(request)
        value=json.dumps(dict(request_id=request['id'],response=response),ensure_ascii=False).encode()
        service.store.write(f"story_ai/responses/{request['id']}.json",value)
        return response
    try:
        response=BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=request['id'],provider=job['provider'],model=job['card']['model'],kind='story',
            price_quote=quote(job['card']),usage_limits=limits(job['card']),operation=operation,actual_charge_jpy=lambda r:charge(job['card'],r)) if paid else operation()
    except Exception:
        try:status=ledger.request_status(request['id']) if paid else None
        except Exception:status={'state':'unknown'}
        state='settled' if status and status['state']=='settled' else 'uncertain' if status else 'not_sent'
        try:set_request(service,request['id'],state=state)
        except (OSError,ValueError,StorageError):pass
        raise StopStory('artifact_pending' if state=='settled' else 'provider_uncertain' if state=='uncertain' else 'not_sent',
            'AI処理が完了しませんでした。応答・請求を保持し、自動再送せず復旧を確認してください。') from None
    _,checksum=receipt(service,request)
    set_request(service,request['id'],state='settled',receipt_sha256=checksum)
    apply_checked(service,request['id'],response)


def apply_checked(service,identifier,response):
    try:apply(service,identifier,response)
    except (OSError,StorageError):
        latest=next(r for r in service.store.load()['requests'] if r['id']==identifier)
        if latest['state']=='applied':return  # metadata committed before an after-write failure
        raise StopStory('artifact_pending','精算済み応答の保存を再開してください。APIは再送しません。') from None
    except ValueError as error:
        set_request(service,identifier,state='invalid')
        raise StopStory('invalid_output',str(error)) from None
