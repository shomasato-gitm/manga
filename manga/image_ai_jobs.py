"""1依頼1候補。応答ファイルを先に保全し、保存だけを同じIDで再開する。"""
import hashlib
import json
import shutil

from manga.image_ai_planning import current
from manga.image_ai_provider import manual_image, mock_image
from manga.image_ai_storage import record
from manga.images import prepare_image
from manga.models import validate_id
from manga.storage import StorageError


def run(service,identifier,*,content=None):
    validate_id(identifier)
    with service.store.locked():
        data=service.store.checked(service.store.load()['revision'])
        job=next((j for j in data['jobs'] if j['id']==identifier),None)
        if not job: raise ValueError('画像依頼が見つかりません。')
        if job['provider'] not in ('mock','manual'): raise ValueError('実API依頼を無料モック経路で実行しません。')
        if job['state']=='ready': return next(c for c in data['candidates'] if c['id']==job['candidate_id'])
        if job['state']!='queued': raise ValueError('待機中の依頼だけを実行できます。保存失敗は同じ依頼を復旧してください。')
        if service.store.safe(f'image_ai/jobs/{identifier}/cancel').exists():
            job['state']='cancelled'; service.store.save(data,'job_cancelled',job_id=identifier); return None
        current(service.directory,service.project_id,job['plan'],hashed=True)
        if shutil.disk_usage(service.store.root).free<32*1024*1024: raise StorageError('画像保存の空き容量が不足しています。')
        job['state']='running'; service.store.save(data,'job_started',job_id=identifier)
        try:
            if job['provider']=='manual':
                if content is not None:
                    if hashlib.sha256(content).hexdigest()!=job['input']['sha256']: raise ValueError('元の手動画像とSHAが異なります。別候補として取り込んでください。')
                    service.store.write(f'image_ai/jobs/{identifier}/input.bin',content)
                source=service.store.read(f'image_ai/jobs/{identifier}/input.bin')
                if hashlib.sha256(source).hexdigest()!=job['input']['sha256']: raise ValueError('手動画像のSHAが一致しません。')
                png,width,height=manual_image(source,job['input']['name'])
            else:
                png,width,height=prepare_image(mock_image(job))
            receipt=dict(job_id=identifier,candidate_id=job['candidate_id'],sha256=hashlib.sha256(png).hexdigest(),width=width,height=height,
                         path=f"image_ai/candidates/{job['candidate_id']}.png",api_calls=0,cost_jpy='0',references_sent_to_external=False)
            service.store.write(receipt['path'],png)
            service.store.write(f'image_ai/jobs/{identifier}/receipt.json',json.dumps(receipt,sort_keys=True).encode(),maximum=128*1024)
            return finish(service,identifier,receipt)
        except (ValueError,OSError,StorageError):
            latest=service.store.load(); saved=next(j for j in latest['jobs'] if j['id']==identifier)
            if saved['state']=='ready': return next(c for c in latest['candidates'] if c['id']==job['candidate_id'])
            saved['state']='artifact_pending'; saved['error']='候補保存が中断しました。同じ依頼IDで保存を再開してください。課金送信はありません。'
            try: service.store.save(latest,'job_interrupted',job_id=identifier)
            except (OSError,ValueError,StorageError): pass
            raise StorageError('画像候補の保存が中断しました。元画像・依頼を保持しています。履歴から保存を再開してください。') from None


def finish(service,identifier,receipt):
    data=service.store.load(); job=next(j for j in data['jobs'] if j['id']==identifier)
    if job['provider'] not in ('mock','manual'): raise ValueError('実API応答は精算確認後に保存してください。')
    expected=f"image_ai/candidates/{job['candidate_id']}.png"
    if receipt['job_id']!=identifier or receipt['candidate_id']!=job['candidate_id'] or receipt['path']!=expected or receipt['api_calls']!=0 or receipt['cost_jpy']!='0': raise StorageError('保存済み応答の対応が不正です。')
    return publish_candidate(service,identifier,receipt,mock=job['provider']=='mock',model='offline-fixture-v1' if job['provider']=='mock' else None)


def publish_candidate(service,identifier,receipt,*,mock,model):
    """精算・応答検証は各実行系で行い、候補の不変保存と明示採用を共用する。"""
    data=service.store.load(); job=next(j for j in data['jobs'] if j['id']==identifier)
    expected=f"image_ai/candidates/{job['candidate_id']}.png"
    if receipt['job_id']!=identifier or receipt['candidate_id']!=job['candidate_id'] or receipt['path']!=expected: raise StorageError('保存済み候補の対応が不正です。')
    candidate=dict(id=job['candidate_id'],saved_at=job['saved_at'],job_id=identifier,path=expected,sha256=receipt['sha256'],width=receipt['width'],height=receipt['height'],
                   provider=job['provider'],mock=mock,model=model)
    service.candidate_bytes(candidate)
    existing=next((c for c in data['candidates'] if c['id']==candidate['id']),None)
    if existing:
        if existing!=candidate: raise StorageError('別の候補を同じIDへ保存しません。')
        if job['state']!='ready' and job['provider']=='gemini-live':
            job['state']='ready'; job['error']=None; service.store.save(data,'candidate_recovered',candidate_id=candidate['id'])
        return existing
    data['candidates'].append(candidate)
    job['state']='cancelled' if service.store.safe(f'image_ai/jobs/{identifier}/cancel').exists() else 'ready'
    job['error']=None
    service.store.save(data,'candidate_saved',candidate_id=candidate['id'])
    return candidate


def recover(service,identifier,*,confirmed=False,content=None):
    if confirmed is not True: raise ValueError('同じ依頼の保存だけを再開することを確認してください。')
    validate_id(identifier)
    rerun=False
    with service.store.locked():
        service.store.checked(service.store.load()['revision'])
        data=service.store.load(); job=next((j for j in data['jobs'] if j['id']==identifier),None)
        if not job: raise ValueError('復旧する依頼を選択してください。')
        if job['provider'] not in ('mock','manual'): raise ValueError('実API依頼を無料モックの再生成で復旧しません。')
        if job['state']=='cancelled': raise ValueError('停止済みです。既存候補は保持しています。')
        path=service.store.safe(f'image_ai/jobs/{identifier}/receipt.json')
        if path.exists(): return finish(service,identifier,json.loads(service.store.read(f'image_ai/jobs/{identifier}/receipt.json',128*1024)))
        if job['state']=='ready': raise StorageError('完了済み応答が欠損しています。初期化せずバックアップを確認してください。')
        # 実APIの再送経路は存在しない。保存前の無料fixture／同じ手動画像だけを再構成。
        job['state']='queued'; job['error']=None; service.store.save(data,'offline_save_resumed',job_id=identifier); rerun=True
    if rerun: return run(service,identifier,content=content)
