"""動画ジョブの直列実行・中断・検証後の原子的確定。API接続なし。"""
import copy
import hashlib
import json
import os
import shutil
import time
from uuid import uuid4

from manga.models import now_iso,validate_id
from manga.storage import StorageError
from manga.voice_models import digest
from manga.japanese_text import find_japanese_font
from manga.video_models import FORMATS,compile_plan
from manga.video_sources import file_bytes
from manga.video_process import VideoError,VideoCancelled,verify_mp4
from manga.video_renderer import render_format,sha_file


def fingerprint(source,plans):
    fonts={k:dict(sha256=sha_file(find_japanese_font(p['font_path']).path),index=find_japanese_font(p['font_path']).index) for k,p in plans.items()}
    extra_ids={e['id'] for p in plans.values() for e in ([p['bgm']] if p['bgm'] else [])+p['sfx']}
    return digest(dict(renderer='manga-ffmpeg-v3',timeline_sha256=source['timeline_sha256'],plans=plans,fonts=fonts,
        extra={i:source['extra'][i]['sha256'] for i in sorted(extra_ids)}))


def create_job(service,timeline_id,kinds,*,expected_revision,acknowledged=False,force=False):
    if not kinds or len(set(kinds))!=len(kinds) or not set(kinds)<=set(FORMATS):raise ValueError('出力形式を重複なく選択してください。')
    data=service.store.checked(expected_revision)
    if timeline_id not in data['plans']:raise ValueError('先に3形式の編集計画を保存してください。')
    plans={k:copy.deepcopy(data['plans'][timeline_id][k]) for k in kinds}
    source=service.inspect(timeline_id,plans);signature=fingerprint(source,plans)
    if acknowledged is not True:raise ValueError('素材・実尺・未確認事項・保存先を確認し、公開前確認が別途必要なことに同意してください。')
    required=128*1024*1024+sum(compile_plan(p,source['timeline'])['duration_ms']*2200 for p in plans.values())
    required+=sum((service.store.root/m['path']).stat().st_size for m in [*source['images'].values(),*source['audio'].values()])
    if shutil.disk_usage(service.store.root).free<required:raise ValueError(f'動画保存の空き容量が不足しています。約{required//1024//1024}MiB以上が必要です。')
    with service.store.lock():
        data=service.store.checked(expected_revision)
        if any(j['state'] in ('queued','running') for j in data['jobs']):raise StorageError('この作品の動画処理が実行中です。完了・中断状態を確認してください。')
        cached=next((j for j in reversed(data['jobs']) if j['fingerprint']==signature and j['state']=='ready'),None)
        reuse=[]
        if cached and not force:
            reuse=[e for e in data['exports'] if e['id'] in cached['export_ids']]
            if len(reuse)!=len(plans):raise VideoError('保存済み動画の履歴が不完全です。削除せず確認してください。')
            for result in reuse:service.export_bytes(result)
        job=dict(id=str(uuid4()),timeline_id=timeline_id,fingerprint=signature,plans=plans,state='ready' if reuse else 'queued',
            created_at=now_iso(),updated_at=now_iso(),warnings=source['warnings'],progress=dict(done=0,total=0,kind=''),
            export_ids=[r['id'] for r in reuse],cache_reused=bool(reuse),error=None)
        data['jobs'].append(job);service.store.save(data,'job_created',job_id=job['id'])
        return copy.deepcopy(job)


def update(service,identifier,**fields):
    with service.store.lock():
        data=service.store.load();job=next(j for j in data['jobs'] if j['id']==identifier)
        job.update(copy.deepcopy(fields),updated_at=now_iso());service.store.save(data,'job_state',job_id=identifier,state=job['state'])


def _publish(service,job,receipt):
    with service.store.source_lock():return _publish_locked(service,job,receipt)


def _publish_locked(service,job,receipt):
    if receipt['job_id']!=job['id'] or receipt['fingerprint']!=job['fingerprint'] or {r['kind'] for r in receipt['results']}!=set(job['plans']):raise VideoError('動画保存記録とジョブの対象が一致しません。')
    source=service.inspect(job['timeline_id'],job['plans'],locked=True)
    if fingerprint(source,job['plans'])!=job['fingerprint']:raise VideoError('制作データが変わりました。古い動画を完成扱いにせず保存を停止しました。')
    with service.store.lock():
        data=service.store.load();target=next(j for j in data['jobs'] if j['id']==job['id'])
        if target['state']=='ready':return [e for e in data['exports'] if e['id'] in target['export_ids']]
        if {k:data['plans'][job['timeline_id']][k] for k in job['plans']}!=job['plans']:raise VideoError('動画の編集計画が変更されています。新しい計画で出力し直してください。')
        results=[]
        for row in receipt['results']:
            kind=row['kind'];relative=f"video/jobs/{job['id']}/{kind}/{kind}.mp4"
            if row['staged']!=relative:raise VideoError('動画保存記録のパスが不正です。')
            staged=service.store.safe(relative)
            if not staged.is_file() or staged.stat().st_size>512*1024*1024 or sha_file(staged)!=row['sha256']:raise VideoError('保存途中のMP4が欠損・変更されています。')
            plan=job['plans'][kind];schedule=compile_plan(plan,source['timeline'])
            metadata=verify_mp4(staged,FORMATS[kind],plan['fps'],schedule['frames']*1000/plan['fps'])
            path=f"video/exports/{job['id']}/{kind}.mp4";destination=service.store.safe(path);destination.parent.mkdir(parents=True,exist_ok=True);service.store.safe(path)
            if destination.exists():
                if sha_file(destination)!=row['sha256']:raise VideoError('完成ファイルの上書きを拒否しました。')
            else:os.link(staged,destination,follow_symlinks=False)
            existing=next((e for e in data['exports'] if e['job_id']==job['id'] and e['kind']==kind),None)
            record=existing or dict(id=str(uuid4()),job_id=job['id'],kind=kind,path=path,sha256=row['sha256'],metadata=metadata,
                timeline_id=job['timeline_id'],fingerprint=job['fingerprint'],created_at=now_iso(),warnings=job['warnings'],
                publication_review_required=True,cache_hits=row['cache_hits'],scenes=row['scenes'])
            if not existing:data['exports'].append(record)
            results.append(record)
        target.update(state='ready',export_ids=[r['id'] for r in results],updated_at=now_iso(),error=None)
        service.store.save(data,'job_ready',job_id=job['id'])
        return results


def run_job(service,job_id):
    validate_id(job_id)
    with service.store.lock(generation=True):
        job=next((j for j in service.store.load()['jobs'] if j['id']==job_id),None)
        if not job or job['state']!='queued':raise VideoError('待機中の動画ジョブを選択してください。')
        directory=service.store.safe(f'video/jobs/{job_id}');directory.mkdir(parents=True,exist_ok=True)
        deadline=time.monotonic()+3600
        def cancel():
            if time.monotonic()>deadline:raise VideoError('動画ジョブ全体の制限時間を超えました。')
            return service.store.safe(f'video/jobs/{job_id}/cancel').exists()
        try:
            if cancel():raise VideoCancelled('開始前に動画処理を中断しました。')
            update(service,job_id,state='running')
            with service.store.source_lock():
                source=service.inspect(job['timeline_id'],job['plans'],locked=True)
                if fingerprint(source,job['plans'])!=job['fingerprint']:raise VideoError('素材・編集計画が変更されました。再検査してください。')
                maps={}
                for category,mapping in (('image',source['images']),('audio',source['audio']),('extra',source['extra'])):
                    maps[category]={}
                    for key,meta in mapping.items():
                        body=file_bytes(service.store.root,meta['path'],25*1024*1024)
                        if hashlib.sha256(body).hexdigest()!=meta['sha256']:raise VideoError('素材コピー中に内容が変更されました。')
                        suffix='png' if category=='image' else 'wav' if category=='audio' else meta['extension']
                        path=service.store.immutable(f'video/jobs/{job_id}/materials/{category}-{key}.{suffix}',body)
                        maps[category][key]=dict(path=path,extension=suffix,duration_ms=meta.get('duration_ms'))
            commands=[];results=[]
            for kind,plan in job['plans'].items():
                if cancel():raise VideoCancelled('動画処理を中断しました。')
                materials=dict(image=lambda key:maps['image'][key]['path'].read_bytes(),
                    audio=lambda key:maps['audio'][key]['path'].read_bytes(),extra=lambda key:maps['extra'][key])
                def progress(done,total):update(service,job_id,progress=dict(kind=kind,done=done,total=total))
                result=render_format(service.store,directory,plan,source['timeline'],find_japanese_font(plan['font_path']),materials,
                    cancel=cancel,progress=progress,commands=commands)
                results.append(result)
            receipt=dict(job_id=job_id,fingerprint=job['fingerprint'],results=results,commands=commands)
            service.store.immutable(f'video/jobs/{job_id}/render_receipt.json',json.dumps(receipt,ensure_ascii=False,indent=2).encode())
            if cancel():raise VideoCancelled('動画処理を中断しました。')
            return _publish(service,job,receipt)
        except (Exception,KeyboardInterrupt) as error:
            state='cancelled' if isinstance(error,(VideoCancelled,KeyboardInterrupt)) else 'failed'
            message=str(error) if isinstance(error,(VideoError,StorageError)) else '動画処理が失敗しました。素材・保存先・空き容量を確認してください。'
            try:update(service,job_id,state=state,error=message)
            except (OSError,ValueError,StorageError):pass
            raise VideoError(message) from None


def recover(service,job_id):
    validate_id(job_id)
    with service.store.lock(generation=True):
        job=next((j for j in service.store.load()['jobs'] if j['id']==job_id),None)
        if not job:raise VideoError('復旧するジョブがありません。')
        if job['state']=='ready':return [e for e in service.store.load()['exports'] if e['id'] in job['export_ids']]
        path=service.store.safe(f'video/jobs/{job_id}/render_receipt.json')
        if not path.is_file():
            update(service,job_id,state='interrupted',error='保存途中の動画は完成品ではありません。設定を確認して無料で再出力してください。')
            raise VideoError('完成した保存記録がないため中断状態を記録しました。再出力は有料APIを呼びません。')
        return _publish(service,job,json.loads(file_bytes(service.store.root,str(path.relative_to(service.store.root)),16*1024*1024)))
