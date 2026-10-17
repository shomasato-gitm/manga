"""保存された検査・目視確認・タスクから現在の確認状況を算出する。"""
from manga.review_context import page_current
from manga.review_models import MANUAL_ITEMS


def latest_scan(data, snapshot, page=None):
    return next((s for s in reversed(data['scans']) if (page_current(s, snapshot, page) if page else s['snapshot']['token'] == snapshot['token'])), None)


def task_current(task, snapshot):
    record = dict(snapshot=task['verified_snapshot'] or task['snapshot'])
    page = task['issue']['page']
    return page_current(record, snapshot, page) if page else record['snapshot']['token'] == snapshot['token']


def task_status(task, snapshot):
    if task['status'] == '確認完了' and not task_current(task, snapshot): return '再確認待ち'
    return task['status']


def unresolved(data, snapshot, page=None):
    return [t for t in data['tasks'] if task_status(t, snapshot) not in ('確認完了', '対応不要') and (page is None or t['issue']['page'] in (None, page))]


def page_status(data, snapshot, page):
    key = str(page)
    manual = data['manual'].get(key)
    current = page_current(manual, snapshot, page)
    answers = manual['answers'] if manual else {k: '未確認' for k in MANUAL_ITEMS}
    scan = latest_scan(data, snapshot, page)
    final = page_current(data['finals'].get(key), snapshot, page)
    pending = unresolved(data, snapshot, page)
    manual_done = current and all(v in ('問題なし', '対象外') for v in answers.values())
    preview = page_current(data['previews'].get(key), snapshot, page)
    scan_done = bool(scan and scan['completed'] and page in scan['rendered_pages'])
    blockers = [i for i in scan['issues'] if i['blocks_export'] and i['page'] in (None, page)] if scan else []
    return dict(page=page, preview=preview, manual_done=manual_done, manual_stale=bool(manual and not current),
                scan=scan, scan_done=scan_done, blockers=blockers, tasks=len(pending),
                final=final and manual_done and preview and scan_done and not blockers and not pending)


def summary(context, data):
    states = [page_status(data, context.snapshot, p['page']) for p in context.pages]
    current_scan = latest_scan(data, context.snapshot)
    adopted = sum(bool(context.artwork['panels'].get(f"{p['page']}:{c['panel']}", {}).get('selected_image_id')) for p in context.pages for c in p['panels']) if context.artwork else 0
    return dict(pages=len(states), panels=sum(len(p['panels']) for p in context.pages), adopted=adopted,
                previews=sum(s['preview'] for s in states), issues=len(current_scan['issues']) if current_scan else None,
                manual_needed=sum(not s['manual_done'] for s in states), tasks=len(unresolved(data, context.snapshot)),
                finals=sum(s['final'] for s in states), states=states,
                unconfirmed_ai=sum(not r['reviewed'] or '未確認' in r['decisions'].values() for r in data['ai_reviews']))


def export_warnings(context, data, pages=None, *, sns_width=None):
    overview = summary(context, data)
    states = [s for s in overview['states'] if pages is None or s['page'] in pages]
    notes = []
    if context.errors: notes.append('読み込めない保存データがあり、品質確認は未完了です。')
    if any(not s['scan_done'] for s in states): notes.append('未検査・古い検査・描画未完了のページがあります。')
    if sns_width and any(not s['scan'] or s['scan']['sns_width'] != sns_width for s in states):
        notes.append(f'SNS横幅{sns_width}pxの解像度検査が未実施です。')
    if any(not s['final'] for s in states): notes.append('未確認の項目が残っています。品質チェックで最終確認していないページがあります。')
    if overview['tasks']: notes.append(f'未解決の修正タスクが{overview["tasks"]}件あります。')
    if overview['unconfirmed_ai']: notes.append('未確認のAI画像レビュー提案があります。AI評価は主観的な参考値です。')
    return notes
