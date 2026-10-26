"""無料音声候補の追記保存。既存ジョブ・staging・復旧を使い、正式割り当ては別操作。"""
import copy
import hashlib
import re
import shutil
from uuid import uuid4

from manga.models import now_iso,validate_id
from manga.storage import StorageError
from manga.voice_audio import inspect_source_wav,normalize_wav,MAX_WAV_BYTES
from manga.voice_models import digest

LOCAL_PROVIDERS=('voicevox','manual-wav')


def rights_record(raw):
    if not isinstance(raw,dict):raise ValueError('音声の出典・利用条件を入力してください。')
    result={}
    for key in ('source','software_terms','library_terms','credit'):
        value=raw.get(key,'')
        if not isinstance(value,str) or not value.strip() or len(value)>2000:raise ValueError('出典・ソフト規約・音声固有の規約・クレジットをすべて入力してください。不要な項目は理由を記録してください。')
        result[key]=value.strip()
    for key in ('software_confirmed','library_confirmed'):
        if type(raw.get(key)) is not bool:raise ValueError('ソフトと音声固有の利用条件を別々に確認してください。')
        result[key]=raw[key]
    result['confirmed']=result['software_confirmed'] and result['library_confirmed']
    return result


def target_for(service,identifier,plan=None):
    plan=plan or service.plan()
    row=next((r for r in plan['rows'] if r['id']==identifier),None)
    if not row or row['needs_review'] or not row['settings']:raise ValueError('先に対象発話の話者・読み・声設定を確認してください。')
    return dict(format='manga-voice-target-v1',project_id=service.project_id,story_version_id=plan['version']['id'],
                utterance_id=row['id'],speaker_id=row['speaker_id'],text=row['text'],spoken_text=row['spoken_text'],
                page=row['page'],panel=row['panel'],cache_key=row['cache_key'],source_token=plan['token'])


def matches(asset,row,version_id):
    target=asset.get('provenance',{}).get('target',{})
    return (asset['cache_key']==row['cache_key'] and asset['story_version_id']==version_id
            and target.get('utterance_id')==row['id'] and target.get('speaker_id')==row['speaker_id']
            and target.get('text')==row['text'] and target.get('spoken_text')==row['spoken_text'])


def validate_local_job(job,project_id):
    p=job['provenance'];t=p['target']
    if t['format']!='manga-voice-target-v1' or t['project_id']!=project_id or t['story_version_id']!=job['story_version_id']:
        raise ValueError('音声取り込み対象の作品・脚本版が不正です。')
    for key in ('project_id','story_version_id','utterance_id','speaker_id'):validate_id(t[key])
    if any(t[k]!=job['utterance'][v] for k,v in (('utterance_id','id'),('speaker_id','speaker_id'),('text','text'),('spoken_text','spoken_text'),('page','page'),('panel','panel'))):
        raise ValueError('音声取り込み対象と発話が一致しません。')
    if t['cache_key']!=job['cache_key'] or t['source_token']!=job['source_token'] or p['original_path']!=f"voice/original/{job['asset_id']}.wav":
        raise ValueError('音声の元データと保存先が不正です。')
    if not re.fullmatch('[0-9a-f]{64}',p['original_sha256'] or '') and not (p['original_sha256'] is None and job['state']!='ready'):
        raise ValueError('音声の取り込み元SHAが不正です。')
    if p['rights']!=rights_record(p['rights']):raise ValueError('音声の利用条件が不正です。')
    if job.get('pricing') is not None or job.get('ledger_path') is not None:raise ValueError('ローカル音声には課金情報を使用しません。')


def original_bytes(service,asset):
    p=asset['provenance'];content=service.store.read_blob(p['original_path'])
    if inspect_source_wav(content)['sha256']!=p['original_sha256']:raise ValueError('取り込み元WAVのSHAが一致しません。元ファイルを確認してください。')
    return content


def candidates(service,identifier):
    data=service.store.load();plan=service.plan(data)
    row=next((r for r in plan['rows'] if r['id']==identifier),None)
    if not row:return []
    return [a for a in data['assets'] if a['settings']['provider'] in LOCAL_PROVIDERS and matches(a,row,plan['version']['id'])]


def _finish(service,job):
    from manga.voice_jobs import _stage,_publish
    content=service.store.read_blob(job['provenance']['original_path'])
    if inspect_source_wav(content)['sha256']!=job['provenance']['original_sha256']:raise ValueError('元WAVのSHAが一致しません。')
    # If both staging files exist, reuse them verbatim; never resynthesize.
    receipt_path=service.store.safe_path(f"voice/staging/{job['id']}.json")
    wav_path=service.store.safe_path(f"voice/staging/{job['id']}.wav")
    if not (receipt_path.is_file() and wav_path.is_file()):
        _stage(service,job,normalize_wav(content),0)
    else:service.store.job_receipt(job)
    return _publish(service,job['id'])


def recover_local(service,job):
    if not job['provenance']['original_sha256']:
        raise ValueError('生成結果を保存できていません。自動再送せず履歴を確認し、使えない処理を明示終了してください。')
    return _finish(service,job)


def prepare(service,identifier,*,target,rights,expected_revision,content=None,consent=False,regenerate=False):
    rights=rights_record(rights)
    original_info=inspect_source_wav(content) if content is not None else None
    with service.store.locked(generation=True):
        with service.store.locked(sources=True):
            data=service.store.checked(expected_revision);plan=service.plan(data)
            current=target_for(service,identifier,plan)
            if not isinstance(target,dict) or target!=current:raise ValueError('対象確認情報の作品・脚本版・発話・話者・読み・声設定が一致しません。現在の発話で取り直してください。')
            row=next(r for r in plan['rows'] if r['id']==identifier)
            provider=row['settings']['provider']
            if provider not in LOCAL_PROVIDERS or (provider=='manual-wav')!=(content is not None):
                raise ValueError('人物別の声設定でWAV手動取り込みまたはVOICEVOXを選択・保存してください。')
            if provider=='voicevox':
                if consent is not True:raise ValueError('この1発話をMacのローカルエンジンへ送信する確認が必要です。')
                if not rights['confirmed']:raise ValueError('ソフトと音声ライブラリの規約を確認してから生成してください。')
                from manga.voicevox import SOFTWARE_TERMS
                if rights['software_terms']!=SOFTWARE_TERMS or 'VOICEVOX' not in rights['credit']:
                    raise ValueError('VOICEVOX公式ソフト規約とVOICEVOXを含む必要なクレジットを記録してください。')
            identity=digest(dict(target=current,rights=rights,original_sha256=original_info['sha256'] if original_info else None))
            if not regenerate:
                previous=next((j for j in reversed(data['jobs']) if j.get('local_key')==identity and j['state']=='ready'),None)
                if previous:
                    asset=next(a for a in data['assets'] if a['job_id']==previous['id']);service.audio_bytes(asset);original_bytes(service,asset)
                    return asset
            pending=next((j for j in data['jobs'] if j['cache_key']==row['cache_key'] and j['state'] not in ('ready','not_sent','closed')),None)
            if pending:raise ValueError('この発話は処理中・復旧待ちです。履歴・復旧から保存を再開してください。')
            if shutil.disk_usage(service.store.root).free<MAX_WAV_BYTES*4:raise StorageError('音声保存の空き容量が不足しています。')
            service.store.check_storage()
            identifier_job=str(uuid4());asset_id=str(uuid4())
            job=dict(id=identifier_job,request_id=identifier_job,asset_id=asset_id,state='running',created_at=now_iso(),
                     story_version_id=plan['version']['id'],source_token=plan['token'],settings=copy.deepcopy(row['settings']),
                     utterance=copy.deepcopy({k:row[k] for k in ('id','speaker_id','text','spoken_text','page','panel','kind')}),
                     cache_key=row['cache_key'],pricing=None,ledger_path=None,local_key=identity,
                     provenance=dict(target=current,rights=rights,original_path=f'voice/original/{asset_id}.wav',
                                     original_sha256=original_info['sha256'] if original_info else None,engine=None))
            data['jobs'].append(job);service.store.save(data,'local_job_started',job_id=job['id'])
        try:
            if content is None:
                from manga.voicevox import Voicevox
                adapter=service.voicevox or Voicevox(job['settings']['origin'])
                content,engine=adapter.synthesize(row['spoken_text'],job['settings'],consent=consent)
                info=inspect_source_wav(content)
                with service.store.locked():
                    data=service.store.load();job=next(j for j in data['jobs'] if j['id']==identifier_job)
                    job['provenance'].update(original_sha256=info['sha256'],engine=engine)
                    service.store.save(data,'local_response_received',job_id=identifier_job)
            service.store.write_blob(job['provenance']['original_path'],content)
            return _finish(service,job)
        except Exception:
            try:
                with service.store.locked():
                    data=service.store.load();job=next(j for j in data['jobs'] if j['id']==identifier_job)
                    job['state']='artifact_pending' if service.store.safe_path(job['provenance']['original_path']).is_file() else 'uncertain'
                    service.store.save(data,'local_job_interrupted',job_id=identifier_job)
            except (ValueError,StorageError,OSError):pass
            raise ValueError('音声を保存できませんでした。履歴・復旧で保存再開を試してください。自動再送・上書きは行いません。') from None
