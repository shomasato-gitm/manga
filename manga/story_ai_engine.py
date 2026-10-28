"""最大5回のPDCAジョブ。停止・再開は同じrequest_idを維持する。"""
from uuid import uuid4
from manga.models import now_iso,validate_id
from manga.storage import StorageError
from manga.story_ai_requests import next_request,execute,receipt,set_request,ledger_for,apply_checked,StopStory


def update(service,identifier,**fields):
    with service.store.lock():
        data=service.store.load();job=next(j for j in data['jobs'] if j['id']==identifier)
        job.update(fields,updated_at=now_iso());service.store.save(data,'job_state',job_id=identifier,state=job['state'])


def run_job(service,identifier,*,project_cap_jpy=None):
    validate_id(identifier)
    with service.store.lock(generation=True):
        job=next((j for j in service.store.load()['jobs'] if j['id']==identifier),None)
        if not job or job['state']!='queued':raise ValueError('待機中の依頼だけを実行できます。途中失敗は保存・請求の復旧を確認してください。')
        update(service,identifier,state='running',error=None,stop_reason=None)
        try:
            while True:
                job=next(j for j in service.store.load()['jobs'] if j['id']==identifier)
                if job['phase']=='done' or job['state'] in ('completed','stopped'):return job
                if service.store.safe(f"story_ai/jobs/{identifier}/cancel-{job['run_epoch']}").exists():
                    update(service,identifier,state='stopped',stop_reason='user_cancelled');return next(j for j in service.store.load()['jobs'] if j['id']==identifier)
                request=next_request(service,identifier)
                execute(service,job,request,project_cap_jpy=project_cap_jpy)
                if job['provider']!='mock':
                    current=next(j for j in service.store.load()['jobs'] if j['id']==identifier)
                    if current['phase']!='done':update(service,identifier,state='stopped',stop_reason='approval_required',error=None)
                    return next(j for j in service.store.load()['jobs'] if j['id']==identifier)
        except StopStory as error:
            update(service,identifier,state='paused' if error.reason in ('provider_uncertain','artifact_pending') else 'stopped',stop_reason=error.reason,error=str(error))
            return next(j for j in service.store.load()['jobs'] if j['id']==identifier)
        except (Exception,KeyboardInterrupt):
            try:update(service,identifier,state='paused',stop_reason='interrupted',error='処理が中断しました。既存応答・予約を保持しています。復旧を確認してください。')
            except (OSError,ValueError,StorageError):pass
            raise ValueError('AI制作が中断しました。保存済み応答と請求を確認してください。自動再送しません。') from None


def recover(service,identifier):
    validate_id(identifier)
    with service.store.lock(generation=True):
        data=service.store.load();job=next((j for j in data['jobs'] if j['id']==identifier),None)
        if not job:raise ValueError('復旧対象がありません。')
        if any(r['job_id']==identifier and r['state']=='invalid' for r in data['requests']):raise ValueError('不正・拒否・未完了の回答です。保存応答を確認し、必要なら明示的な新規依頼を作成してください。自動修復・再送はしません。')
        for request in [r for r in data['requests'] if r['job_id']==identifier and r['state'] in ('sending','uncertain','settled')]:
            if job['provider']!='mock':
                ledger=ledger_for(service,job);status=ledger.request_status(request['id'])
                if not status or status['project_id']!=service.project_id or status['state']!='settled':raise ValueError('請求が未確定です。予算管理で明細照合してから保存を再開してください。APIは再送しません。')
            response,checksum=receipt(service,request)
            set_request(service,request['id'],state='settled',receipt_sha256=checksum)
            apply_checked(service,request['id'],response)
        current=next(j for j in service.store.load()['jobs'] if j['id']==identifier)
        if current['phase']!='done':update(service,identifier,state='stopped',stop_reason='interrupted',error='保存を確認しました。続行は別の明示操作です。')
        return next(j for j in service.store.load()['jobs'] if j['id']==identifier)


def resume(service,identifier,*,confirmed=False):
    if confirmed is not True:raise ValueError('残りの処理と予算上限を確認してください。')
    with service.store.lock(generation=True),service.store.source_lock(),service.store.lock():
        data=service.store.load();job=next((j for j in data['jobs'] if j['id']==identifier),None)
        if not job or job['phase']=='done' or job['state'] in ('queued','running','completed'):raise ValueError('再開できる停止中の依頼を選択してください。')
        if job['provider']!='mock':raise ValueError('実APIの続行には「次の1送信」の見積・個別承認が必要です。')
        if any(r['job_id']==identifier and r['state'] not in ('applied','queued','not_sent') for r in data['requests']):raise ValueError('未確定・不正な応答があります。先に復旧・請求を確認してください。')
        if service.source(job['settings'])[1]!=job['source_token']:raise ValueError('制作条件が古くなっています。新しい依頼を作成してください。')
        if job['provider']!='mock' and not service.paid_enabled():raise ValueError('有料機能は無効です。')
        job.update(state='queued',run_epoch=str(uuid4()),error=None);service.store.save(data,'job_resumed',job_id=identifier)
        return job
