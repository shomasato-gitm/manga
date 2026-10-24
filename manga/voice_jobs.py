"""保存済みジョブを一度だけ実行し、課金後の保存失敗から再送なしで復旧する。"""
import copy
import json
import shutil
from uuid import uuid4

from manga.budget import BudgetedExecutor
from manga.budget.config import open_budget
from manga.models import now_iso
from manga.storage import StorageError
from manga.voice_audio import MockVoice, pcm_wav, inspect_wav, verify_with_ffprobe, MAX_WAV_BYTES
from manga.voice_polly import PollyVoice, credentials, polly_ssml
from manga.voice_pricing import price_quote, usage_limits, converted_charge
from manga.voice_models import speech_text


def _publish(service,job_id):
    store=service.store
    with store.locked():
        data=store.load();job=next(j for j in data['jobs'] if j['id']==job_id)
        if job['state']=='ready':return next(a for a in data['assets'] if a['job_id']==job_id)
        receipt,content=store.job_receipt(job)
        info=inspect_wav(content)
        path=f"voice/audio/{job['asset_id']}.wav"
        target=store.write_blob(path,content)
        info=verify_with_ffprobe(target,info)
        existing=next((a for a in data['assets'] if a['id']==job['asset_id']),None)
        asset=existing or dict(id=job['asset_id'],job_id=job_id,path=path,cache_key=job['cache_key'],
                   speaker_id=job['utterance']['speaker_id'],settings=copy.deepcopy(job['settings']),info=info,
                   created_at=now_iso(),mock=job['settings']['provider']=='mock',story_version_id=job['story_version_id'])
        if not existing:
            asset['rights']=dict(kind='test-tone' if asset['mock'] else 'provider-stock-voice',
                terms_url=job['pricing']['terms_url'] if job['pricing'] else None,
                reviewed_on=job['pricing']['verified_on'] if job['pricing'] else None,
                status='test-only' if asset['mock'] else 'user-attested')
        if existing and (existing['job_id']!=job_id or existing['cache_key']!=job['cache_key'] or existing['info']['sha256']!=info['sha256']):
            raise StorageError('異なる音声素材の上書きを停止しました。')
        if not existing:data['assets'].append(asset)
        data['assignments'][job['utterance']['id']]=asset['id']
        before=job['state'];job['state']='ready'
        store.save(data,'job_ready',before=before,job_id=job_id)
        return asset


def _stage(service,job,wav,characters):
    info=inspect_wav(wav)
    receipt=dict(job_id=job['id'],asset_id=job['asset_id'],info=info,billed_characters=characters)
    # Metadata first: a complete staging pair is recoverable without another API call.
    service.store.write_blob(f"voice/staging/{job['id']}.json",json.dumps(receipt,ensure_ascii=False).encode())
    service.store.write_blob(f"voice/staging/{job['id']}.wav",wav)
    return receipt


def generate(service,ids,*,regenerate=False,approval=None,progress=None):
    estimate=service.estimate(ids,regenerate=regenerate)
    plan=service.plan()
    ledger=None
    if estimate['paid_calls']:
        if not service.paid_enabled():raise ValueError('有料TTSは既定で無効です。別途の明示承認前に実行しません。')
        if approval!=estimate['approval_key']:raise ValueError('現在の対象・上限見積に対する明示承認が必要です。')
        if not plan['confirmed']:raise ValueError('現在の脚本版を人間が確定してから有料音声を生成してください。')
        if service.polly is None or service.polly.keys is None:credentials()
        ledger=open_budget(service.directory)
        previous={j['ledger_path'] for j in service.store.load()['jobs'] if j['settings']['provider']!='mock'}
        if previous and previous!={str(ledger.path.resolve())}:
            raise ValueError('予算台帳の保存先が生成履歴と異なります。元の台帳を確認し、無断で0円から再開しないでください。')
        preview=BudgetedExecutor(ledger).dry_run(project_id=service.project_id,quote_jpy=estimate['max_jpy'],provider='aws-polly')
        if not preview['possible']:raise ValueError('作品・月間・プロバイダー予算、または請求確認待ちにより生成できません。')
    results=[]
    with service.store.locked(generation=True):
        for index,identifier in enumerate(ids):
            with service.store.locked(sources=True):
                data=service.store.load();current=service.plan(data)
                if current['token']!=estimate['token']:raise ValueError('制作条件が変更されました。残りの音声を送信せず停止しました。')
                row=next(r for r in current['rows'] if r['id']==identifier)
                if row['asset'] and not regenerate:
                    service.audio_bytes(row['asset'])
                    data['assignments'][identifier]=row['asset']['id']
                    service.store.save(data,'cache_reuse',utterance_id=identifier,asset_id=row['asset']['id'])
                    results.append(dict(asset=row['asset'],cache_hit=True))
                    if progress:progress(index+1,len(ids))
                    continue
                pending=next((j for j in data['jobs'] if j['cache_key']==row['cache_key'] and j['state'] not in ('ready','not_sent','closed')),None)
                if pending:raise ValueError('同じ音声の処理が未確定です。再送せず履歴・復旧で確認してください。')
                if shutil.disk_usage(service.store.root).free<MAX_WAV_BYTES*2:
                    raise StorageError('音声保存の空き容量が不足しています。API送信前に停止しました。')
                service.store.check_storage()
                job_id=str(uuid4())
                job=dict(id=job_id,request_id=job_id,asset_id=str(uuid4()),state='queued',created_at=now_iso(),
                         story_version_id=current['version']['id'],source_token=current['token'],settings=copy.deepcopy(row['settings']),
                         utterance=copy.deepcopy({k:row[k] for k in ('id','speaker_id','text','spoken_text','page','panel','kind')}),
                         cache_key=row['cache_key'],pricing=copy.deepcopy(data['pricing']) if row['settings']['provider']!='mock' else None,
                         ledger_path=str(ledger.path.resolve()) if row['settings']['provider']!='mock' else None)
                data['jobs'].append(job);service.store.save(data,'job_created',job_id=job_id)
                job['state']='running';service.store.save(data,'job_running',job_id=job_id)
            paid=job['settings']['provider']=='aws-polly'
            try:
                if not paid:
                    _stage(service,job,MockVoice().synthesize(speech_text(row['spoken_text']),row['settings']),0)
                else:
                    ssml=polly_ssml(row['spoken_text'],row['settings'])
                    quote=price_quote(job['pricing'],row['settings']['engine'])
                    adapter=service.polly or PollyVoice()
                    def operation():
                        pcm,characters=adapter.synthesize(ssml,row['settings'])
                        return _stage(service,job,pcm_wav(pcm),characters)
                    BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=job_id,
                        provider='aws-polly',model=row['settings']['engine'],kind='tts',
                        price_quote=quote,usage_limits=usage_limits(ssml),operation=operation,
                        actual_charge_jpy=lambda receipt:converted_charge(quote,receipt['billed_characters']))
                asset=_publish(service,job_id)
            except BaseException:
                # No provider exception text, keys, headers or response bodies enter JSON/logs.
                try:
                    status=ledger.request_status(job_id) if paid else None
                    state='artifact_pending' if status and status['state']=='settled' else 'uncertain' if status else 'not_sent'
                except Exception:state='uncertain'
                try:
                    with service.store.locked():
                        failed=service.store.load();target=next(j for j in failed['jobs'] if j['id']==job_id)
                        target['state']=state;service.store.save(failed,'job_interrupted',job_id=job_id,state=state)
                except (OSError,StorageError,ValueError):pass  # durable running record still prevents re-send
                raise ValueError('音声処理が完了しませんでした。履歴・復旧を確認してください。有料呼び出しを自動再送しません。') from None
            results.append(dict(asset=asset,cache_hit=False))
            if progress:progress(index+1,len(ids))
    return results


def recover(service,job_id):
    with service.store.locked(generation=True):
        data=service.store.load();job=next((j for j in data['jobs'] if j['id']==job_id),None)
        if job is None:raise ValueError('復旧対象の音声ジョブがありません。')
        if job['state']=='ready':return next(a for a in data['assets'] if a['job_id']==job_id)
        if job['settings']['provider']!='mock':
            ledger=open_budget(service.directory)
            if str(ledger.path.resolve())!=job['ledger_path']:raise ValueError('予算台帳の保存先が生成時と異なります。元の台帳で請求を照合してください。')
            status=ledger.request_status(job_id)
            if not status or status['project_id']!=service.project_id or status['state']!='settled':
                raise ValueError('請求が未確定です。予算台帳の明細照合後に保存を再開してください。APIは再送しません。')
        return _publish(service,job_id)


def close_resolved_job(service,job_id,*,confirmed=False):
    if confirmed is not True:raise ValueError('音声が使えないことと、次の生成で再課金され得ることを確認してください。')
    with service.store.locked(generation=True):
        with service.store.locked():
            data=service.store.load();job=next((j for j in data['jobs'] if j['id']==job_id),None)
            if job is None or job['state'] in ('ready','closed'):raise ValueError('終了する未完了ジョブを選択してください。')
            if job['settings']['provider']!='mock':
                ledger=open_budget(service.directory)
                if str(ledger.path.resolve())!=job['ledger_path']:raise ValueError('予算台帳の保存先が生成時と異なります。')
                status=ledger.request_status(job_id)
                if status is None and job['state']!='not_sent':raise ValueError('生成時の請求記録が見つかりません。台帳のバックアップと明細を確認してください。')
                if status and (status['project_id']!=service.project_id or status['state'] not in ('settled','cancelled')):
                    raise ValueError('請求が未確定です。先に台帳で明細を照合してください。')
            before=job['state'];job['state']='closed'
            service.store.save(data,'job_closed_after_review',before=before,job_id=job_id)
