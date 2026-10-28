"""初回1枚と、根拠・対象・件数を固定した追加送信の承認。正本はImageAIStore。"""
import copy
from decimal import Decimal

from manga.gemini_image_contract import MODEL, validate_card, bounds
from manga.image_ai_storage import record
from manga.models import validate_id
from manga.storage import StorageError
from manga.voice_models import digest


def contract_id(card):
    # 同じ確認済みモデル内の比率/思考変更は毎回再見積。初回通信を重複させない。
    return digest(dict(model=MODEL,contract_version=1))


def save_configuration(service, card, *, expected_revision):
    from manga.image_ai_live_pricing import quotation
    validate_card(card); quotation(card)
    with service.store.locked():
        data=service.store.checked(expected_revision)
        old=next((e for e in reversed(data['events']) if e['kind']=='lite_configuration'),None)
        if old and old['card']==card: return old
        event=record(kind='lite_configuration',card=copy.deepcopy(card))
        data['events'].append(event); service.store.save(data,'lite_configuration_saved')
        return event


def configuration(service):
    event=next((e for e in reversed(service.store.load()['events']) if e['kind']=='lite_configuration'),None)
    return copy.deepcopy(event['card']) if event else None


def has_first_review(service, card):
    from manga.image_ai_live_plan import shared_ledger
    data=service.store.load()
    for event in data['events']:
        if event['kind']!='lite_first_reviewed' or event['contract_id']!=contract_id(card) or event['test_only'] is not service.live_adapter.fake: continue
        candidate=next((c for c in data['candidates'] if c['id']==event['candidate_id']),None)
        job=next((j for j in data['jobs'] if j['id']==event['job_id']),None)
        if not candidate or not job or job['state']!='ready' or candidate['job_id']!=job['id'] or candidate['sha256']!=event['sha256'] or candidate['mock'] is not event['test_only']: continue
        service.candidate_bytes(candidate)
        status=shared_ledger(service,expected=job['live']['ledger_path']).request_status(job['id'])
        if status and status['state']=='settled' and status['project_id']==service.project_id and status['provider']=='gemini-live' and status['model']==MODEL: return True
    return False


def confirm_first(service, candidate_id, *, confirmed=False, test_only=False):
    if confirmed is not True or type(test_only) is not bool or test_only is not service.live_adapter.fake:
        raise ValueError('初回1枚の画像・usage・料金と、テスト/実通信の区分を確認してください。')
    from manga.image_ai_live_jobs import get_job
    from manga.image_ai_live_plan import shared_ledger
    with service.store.locked():
        data=service.store.checked(service.store.load()['revision'])
        candidate=next((c for c in data['candidates'] if c['id']==candidate_id),None)
        if not candidate or candidate['model']!=MODEL or candidate['mock'] is not test_only:
            raise ValueError('実通信の確認に無料モック・フェイク画像は使えません。')
        job=get_job(service,candidate['job_id']); service.candidate_bytes(candidate)
        if job['state']!='ready' or job['live']['fake'] is not test_only: raise ValueError('保存・精算を完了した初回画像を選んでください。')
        if not any(r['candidate_id']==candidate_id and r['sha256']==candidate['sha256'] for r in data['reviews']):
            raise ValueError('初回画像の目視・権利確認を先に記録してください。')
        status=shared_ledger(service,expected=job['live']['ledger_path']).request_status(job['id'])
        if not status or status['state']!='settled' or status['project_id']!=service.project_id: raise ValueError('初回の請求・保存を照合してください。')
        old=next((e for e in data['events'] if e['kind']=='lite_first_reviewed' and e['job_id']==job['id']),None)
        if old: return old
        event=record(kind='lite_first_reviewed',job_id=job['id'],candidate_id=candidate_id,sha256=candidate['sha256'],
            contract_id=contract_id(job['live']['card']),test_only=test_only)
        data['events'].append(event); service.store.save(data,'lite_first_confirmed')
        return event


def target(plan):
    return dict(kind=plan['kind'],character_id=plan['character_id'],page=plan['page'],panel=plan['panel'],
                material_token=plan['material_token'],prompt_sha256=plan['prompt_sha256'],
                references=[{k:r[k] for k in ('id','character_id','sha256')} for r in plan['reference_images']])


def preview(service, plans, card, *, project_cap='500'):
    from manga.image_ai_live_plan import prepare, shared_ledger
    if not isinstance(plans,list) or not 1<=len(plans)<=24: raise ValueError('送信対象は1〜24枚にしてください。')
    cap=Decimal(str(project_cap))
    if not cap.is_finite() or not 0<cap<=500: raise ValueError('作品の最大予算は0円より大きく500円以下です。')
    rows=[]; maximum=Decimal('0')
    for plan in plans:
        quote,_=prepare(service,plan,card,[r['id'] for r in plan['reference_images']])
        rows.append(target(plan)); maximum+=Decimal(quote['max_jpy'])
    if len({digest(r) for r in rows})!=len(rows): raise ValueError('同じ画像対象が重複しています。')
    if len(plans)>1 and not has_first_review(service,card): raise ValueError('最初の実通信は1枚だけです。保存・料金・画像の確認後に一括承認してください。')
    summary=shared_ledger(service).summary(service.project_id)
    value=dict(project_id=service.project_id,card_sha256=digest(card),targets=rows,count=len(rows),max_jpy=str(maximum),
        project_cap_jpy=str(cap),fake=service.live_adapter.fake,
        ledger_path=str(shared_ledger(service).path.resolve()))
    value['possible']=not summary['blocked'] and summary['project_used_jpy']+maximum<=cap and maximum<=summary['monthly_remaining_jpy']
    value['token']=digest({k:v for k,v in value.items() if k!='possible'})
    return value


def approve(service, plans, card, *, token, project_cap='500', confirmed=False, expected_revision):
    if confirmed is not True: raise ValueError('件数・全対象・参照画像の外部送信・最大費用・停止条件を明示承認してください。')
    with service.store.locked():
        data=service.store.checked(expected_revision)
        value=preview(service,plans,card,project_cap=project_cap)
        if value['token']!=token or not value['possible']: raise ValueError('対象変更・残予算不足・請求未確定のため承認できません。再見積または少ないページ数・手動方式を選んでください。')
        old=next((e for e in data['events'] if e['kind']=='lite_scope' and e['scope']['token']==token),None)
        if old: return old['id']
        event=record(kind='lite_scope',scope=value)
        data['events'].append(event); service.store.save(data,'lite_scope_approved')
        return event['id']


def require_scope(service, plan, card, scope_id, *, identifier=None):
    from manga.image_ai_live_plan import shared_ledger
    jobs=[j for j in service.store.load()['jobs'] if j['provider']=='gemini-live' and j['live']['card']['model']==MODEL and j['id']!=identifier]
    proof=has_first_review(service,card)
    if not proof:
        if jobs: raise ValueError('初回1枚が未確認です。画像・保存・請求の確認まで追加送信しません。')
        if scope_id is None: return  # 初回だけは従来の1枚見積・二段階承認で実行可。
    if scope_id is None: raise ValueError('追加画像には対象と件数を固定した明示承認が必要です。')
    validate_id(scope_id)
    event=next((e for e in service.store.load()['events'] if e['kind']=='lite_scope' and e['id']==scope_id),None)
    if not event: raise ValueError('画像の一括承認が見つかりません。')
    value=event['scope']
    if value['token']!=digest({k:v for k,v in value.items() if k not in ('possible','token')}) or value['project_id']!=service.project_id or value['fake'] is not service.live_adapter.fake or value['card_sha256']!=digest(card):
        raise ValueError('承認した作品・料金・実通信区分と一致しません。')
    if not proof and value['count']!=1: raise ValueError('初回は1枚に限ります。')
    if target(plan) not in value['targets']: raise ValueError('承認対象外または変更された画像です。再見積してください。')
    used=[j for j in jobs if j['live'].get('scope_id')==scope_id]
    if len(used)>=value['count'] or any(target(j['plan'])==target(plan) for j in used):
        raise ValueError('この対象の送信枠は使用済みです。二重保存・自動再送しません。')
    ledger=shared_ledger(service,expected=value['ledger_path']); summary=ledger.summary(service.project_id)
    from manga.image_ai_live_pricing import quotation
    if summary['blocked'] or summary['project_used_jpy']+Decimal(quotation(card)['max_jpy'])>Decimal(value['project_cap_jpy']):
        raise ValueError('承認した作品予算または請求確認待ちで停止しました。')


def find_scope(service,plan,card):
    for event in reversed(service.store.load()['events']):
        if event['kind']!='lite_scope' or event['scope']['card_sha256']!=digest(card) or target(plan) not in event['scope']['targets']: continue
        try: require_scope(service,plan,card,event['id'])
        except ValueError: continue
        return event['id']
    return None


def validate_events(data):
    for event in data['events']:
        if event.get('kind')=='lite_configuration': validate_card(event['card'])
        elif event.get('kind')=='lite_first_reviewed':
            from manga.image_ai_models import sha
            validate_id(event['job_id']); validate_id(event['candidate_id']); sha(event['sha256']); sha(event['contract_id'])
            if type(event['test_only']) is not bool: raise ValueError
        elif event.get('kind')=='lite_scope':
            scope=event['scope']
            if scope['project_id']!=data['project_id'] or type(scope['count']) is not int or not 1<=scope['count']<=24 or scope['count']!=len(scope['targets']) or type(scope['fake']) is not bool or scope['token']!=digest({k:v for k,v in scope.items() if k not in ('possible','token')}): raise ValueError
