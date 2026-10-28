"""漫画制作と既存の画像Serviceをつなぐ。送信・精算・画像保存は複製しない。"""
from decimal import Decimal

from manga.storage import StorageError
from manga.voice_models import digest


def enabled(engine):
    return bool(engine.load()['plan'].get('image_config'))


def card(engine):
    value = engine.images.lite_configuration()
    if not enabled(engine) or not value:
        raise ValueError('この制作では画像APIを選択していません。')
    return value


def image_plan(engine, kind, **selection):
    price = card(engine)
    return engine.images.plan(kind, options={'size': f"{price['width']}x{price['height']}"}, **selection)


def same_target(left, right):
    return all(left[k] == right[k] for k in ('kind', 'character_id', 'page', 'panel', 'view', 'expression'))


def prior_job(engine, plan):
    # 変更前の失敗も履歴に残す。請求・保存が未解決なら新しい依頼で迂回しない。
    return next((j for j in reversed(engine.images.store.load()['jobs'])
                 if j['provider'] == 'gemini-live' and same_target(j['plan'], plan)), None)


def next_image(engine, kind, **selection):
    from manga.manga_mvp_steps import action
    plan = image_plan(engine, kind, **selection)
    job = prior_job(engine, plan)
    if job:
        if job['state'] == 'queued':
            from manga.image_ai_live_plan import shared_ledger
            if shared_ledger(engine.images).summary(engine.project_id)['blocked']:
                return None, '共通予算の請求が未確定です。送信せず照合を待ちます。'
            if job['live']['card'] != card(engine):
                return None, '待機中の画像依頼と料金設定が異なります。元の承認条件を確認してください。'
            return action('reference' if kind == 'character' else 'panel', 'image/' + job['id'], **selection), ''
        if job['state'] != 'ready':
            return None, '画像の送信・請求・保存が未解決です。共通予算で照合後、「保存だけを復旧」を操作してください。自動再送しません。'
        raise StorageError('保存済み画像の対応を確認できません。既存の画像履歴で確認してください。')
    price = card(engine)
    if not engine.images.lite_first_confirmed(price) and engine.images.store.load()['jobs']:
        return None, '初回1枚の画像・保存・usage・精算を確認してください。確認前に2枚目を送信しません。'
    scope = engine.images.find_lite_scope(plan, price)
    if not scope:
        return None, '画像の送信対象・参照画像・最大料金を下で確認し、今回の範囲を承認してください。'
    return action('reference' if kind == 'character' else 'panel', 'image/' + digest(plan), **selection), ''


def batch(engine):
    """現在の依存段階だけを見積。基準画像を採用する前にコマ送信権を作らない。"""
    from manga.manga_mvp_steps import check_story, selected, image_candidates, pending_candidates
    if not enabled(engine) or engine.plan()['changed']:
        return []
    price = card(engine)
    latest = check_story(engine)
    if not latest:
        return []
    people = [p for p in engine.story.artwork.load()['characters'] if not p['deleted_at']]
    if not people:
        return []
    first = engine.images.lite_first_confirmed(price)
    jobs = engine.images.store.load()['jobs']
    result = []
    references = image_candidates(engine, 'character')
    for person in people:
        if selected(engine, 'character', person['id']):
            continue
        if any(next(j for j in jobs if j['id'] == c['job_id'])['plan']['character_id'] == person['id'] for c in references):
            continue
        plan = image_plan(engine, 'character', character_id=person['id'])
        if prior_job(engine, plan):
            return []
        result.append(plan)
    if result:
        if not first and jobs:
            return []
        return result if first else result[:1]
    if pending_candidates(engine, 'character'):
        return []
    # 全基準画像・バイブル・作画指示・ページ構成を既存計画で再検証する。
    art = engine.story.artwork.load()
    pages = engine.pages.load()['pages']
    candidates = image_candidates(engine, 'panel')
    for page in latest['story']['pages']:
        if str(page['page']) not in pages:
            return []
        for panel in page['panels']:
            p, c = page['page'], panel['panel']
            if selected(engine, 'panel', f'{p}:{c}'):
                continue
            if any(next(j for j in jobs if j['id'] == i['job_id'])['plan']['page'] == p and
                   next(j for j in jobs if j['id'] == i['job_id'])['plan']['panel'] == c for i in candidates):
                continue
            if f'{p}:{c}' not in art['panels']:
                return []
            plan = image_plan(engine, 'panel', page=p, panel=c)
            if prior_job(engine, plan):
                return []
            result.append(plan)
    return result[:24]


def preview(engine):
    plans = batch(engine)
    if not plans:
        return None
    value = engine.images.preview_lite_scope(plans, card(engine), project_cap=engine.load()['plan'].get('max_budget_jpy', '500'))
    value['plans'] = plans
    return value


def execute(engine, kind, **selection):
    from manga.image_ai_live_plan import shared_ledger
    plan = image_plan(engine, kind, **selection)
    price = card(engine)
    ledger = shared_ledger(engine.images)
    estimate = engine.images.estimate_live(plan, price, [r['id'] for r in plan['reference_images']])
    cap = Decimal(engine.load()['plan'].get('max_budget_jpy', '500'))
    summary = ledger.summary(engine.project_id)
    if summary['blocked'] or summary['project_used_jpy'] + Decimal(estimate['max_jpy']) > cap:
        raise ValueError('選択した最大予算に達しました。自動送信を停止し、手動画像へ切り替えられます。')
    job = prior_job(engine, plan)
    if not job:
        scope = engine.images.find_lite_scope(plan, price)
        if not scope:
            raise ValueError('画像の個別または一括承認が必要です。')
        job = engine.images.create_live_job(plan, price, [r['id'] for r in plan['reference_images']],
            approval_token=estimate['approval_token'], confirmed=True, expected_revision=engine.images.store.load()['revision'], scope_id=scope)
    if job['state'] == 'queued':
        engine.images.run(job['id'], approved=True)
    elif job['state'] != 'ready':
        raise ValueError('既存依頼は再送しません。請求照合と保存復旧が必要です。')
