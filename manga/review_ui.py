"""完成・書き出し工程内の品質確認。選択した作業だけを構築する。"""
from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store
from manga.storage import StorageError
from manga.review_context import load_context
from manga.review_storage import ReviewStore
from manga.review_status import summary, unresolved, latest_scan, task_status, export_warnings
from manga.review_ui_common import perform, open_issue, issue_label, issue_rows

VIEWS = ('自動チェック・最終確認', 'ページ一覧・手動レビュー', '修正タスク', 'ChatGPT画像レビュー（任意）', '確認履歴')


def render_review(project, directory, navigate):
    st.header('品質チェック・修正管理')
    st.caption('自動検査と人間による目視確認を分けて管理します。未検査・未確認は「問題なし」になりません。')
    prefix = f'review_{project.id}'
    notice = st.empty()
    if '_review_notice' in st.session_state: notice.success(st.session_state.pop('_review_notice'))
    store = ReviewStore(directory, project.id)
    try:
        context = load_context(directory, project.id)
        data = read_store(store)
    except StorageError as error:
        st.error(str(error)); return
    overview = summary(context, data)
    stats = [('ページ / コマ', f'{overview["pages"]} / {overview["panels"]}'), ('画像採用済み', f'{overview["adopted"]}コマ'),
             ('プレビュー目視確認', f'{overview["previews"]}ページ'), ('自動検出', f'{overview["issues"]}件' if overview['issues'] is not None else '未検査 / 要再検査'),
             ('手動レビュー必要', f'{overview["manual_needed"]}ページ'), ('未解決タスク', f'{overview["tasks"]}件'), ('最終確認済み', f'{overview["finals"]}ページ')]
    columns = st.columns(4)
    for index, (label, value) in enumerate(stats): columns[index % 4].metric(label, value)
    _next_problem(project, context, data, navigate)
    jump = st.session_state.pop(f'{prefix}_jump', None)
    if jump:
        st.session_state[f'{prefix}_view'] = '修正タスク' if jump.get('task_id') else 'ページ一覧・手動レビュー'
        if jump.get('page') in [p['page'] for p in context.pages]: st.session_state[f'{prefix}_page'] = jump['page']
        if jump.get('task_id'): st.session_state[f'{prefix}_task_pending'] = jump['task_id']
    view = st.radio('品質チェックの作業', VIEWS, horizontal=True, key=f'{prefix}_view')
    with st.empty().container():
        if view == VIEWS[0]: _automatic(project, context, store, data, overview, prefix, navigate)
        elif view == VIEWS[1]:
            from manga.review_page_ui import render_pages
            render_pages(project, context, store, data, prefix, navigate)
        elif view == VIEWS[2]:
            from manga.review_task_ui import render_tasks
            render_tasks(project, context, store, data, prefix, navigate)
        elif view == VIEWS[3]:
            from manga.review_ai_ui import render_ai_review
            render_ai_review(project, context, store, data, prefix)
        else:
            st.caption('以前の検査・目視回答・修正状態・AI提案を削除せず保持しています。')
            st.download_button('品質履歴JSONをダウンロード', __import__('json').dumps(data, ensure_ascii=False, indent=2), file_name='review.json', mime='application/json')
            for name, label in [('scans', '自動検査'), ('ai_requests', '画像レビュー依頼'), ('ai_reviews', 'AI画像レビュー'), ('events', '確認・対応の変更履歴')]:
                with st.expander(f'{label}（{len(data[name])}件）'):
                    from manga.gallery_ui import page_items
                    for row in page_items(list(reversed(data[name])), f'{prefix}_history_{name}', label, size=5): st.json(row, expanded=False)


def _next_problem(project, context, data, navigate):
    tasks = sorted(unresolved(data, context.snapshot), key=lambda t: (('高', '中', '低').index(t['issue']['severity']), t['created_at']))
    latest = latest_scan(data, context.snapshot)
    issue, task_id = None, None
    if tasks:
        issue, task_id = tasks[0]['issue'], tasks[0]['id']
        state = task_status(tasks[0], context.snapshot)
    elif latest and latest['issues']:
        handled = {t['issue']['id'] for t in data['tasks'] if task_status(t, context.snapshot) == '対応不要' or (task_status(t, context.snapshot) == '確認完了')}
        remaining = [i for i in latest['issues'] if i['id'] not in handled]
        if remaining:
            issue = sorted(remaining, key=lambda i: ('高', '中', '低').index(i['severity']))[0]
            state = '検出・未確認'
    if issue:
        st.subheader('次に修正する問題')
        st.warning(f'{issue_label(issue)} ｜ 重要度：{issue["severity"]} ｜ 状態：{state}')
        if st.button('対象の編集画面を開く', type='primary', key='review_next_fix'):
            from manga.review_models import target
            try: target(issue, context.pages, global_ok=True)
            except ValueError: st.warning('対象ページ・コマは現在の脚本から外れています。履歴とタスクを確認してください。')
            else: open_issue(project.id, issue, navigate, task_id=task_id)
    else:
        st.info('次の操作：自動チェックを実行し、各ページを目視レビューしてください。検査未実施は合格を意味しません。')


def _automatic(project, context, store, data, overview, prefix, navigate):
    from manga.review_checks import scan
    from manga.review_assets import save_thumbnails
    st.subheader('1. 保存データの自動チェック')
    for error in context.errors: st.error(error['message'])
    st.caption('顔・人体・背景の画像認識は行いません。解像度は保存済みの出力サイズ・コマ寸法・トリミング倍率で判定します。')
    sns = st.selectbox('解像度チェックの対象', [None, 600, 1080], format_func=lambda n: f'ページPNG・PDF ＋ 縦読みSNS 横幅{n}px' if n else 'ページPNG・PDF（保存済み出力サイズ）', key=f'{prefix}_scan_sns')
    if st.button('自動チェックを実行', type='primary'):
        meter = st.progress(0, text='検査対象を確認しています')
        def advance(done, total): meter.progress(done/total, text=f'検査済み {done}/{total}ページ')
        def action():
            result = scan(context, sns_width=sns, progress=advance)
            store.save_scan(result, expected_revision=data['revision'])
        perform(action, '自動チェック結果を保存しました。')
    current = latest_scan(data, context.snapshot)
    if current:
        st.caption(f'検査日時：{current["created_at"]} ｜ 実行した検査：' + ' / '.join(current['checks']))
        if not current['completed']: st.warning('一部の検査を実行できていません。データやフォントを修正して再検査してください。')
        if current['issues']:
            st.dataframe(issue_rows(current['issues']), hide_index=True, use_container_width=True)
            selected = st.selectbox('確認する検出結果', current['issues'], format_func=issue_label, key=f'{prefix}_auto_issue_{current["id"]}')
            col1, col2 = st.columns(2)
            if col1.button('検出箇所を編集する'): open_issue(project.id, selected, navigate)
            if col2.button('この検出結果を修正タスクにする'):
                perform(lambda: store.create_task(selected, origin='auto', source_id=current['id'], expected_revision=data['revision'], snapshot=context.snapshot), '修正タスクを登録しました。')
        else: st.success('実行した自動チェックでは問題を検出しませんでした。目視レビューは別途必要です。')
    else: st.warning('現在のデータに対する検査結果はありません。以前の結果は履歴に保持しています。')
    if context.pages and st.button('ページサムネイルを作成・更新'):
        meter = st.progress(0, text='縮小プレビューを準備しています')
        with operation('ページを順番に縮小描画しています'):
            messages = save_thumbnails(context, progress=lambda done, total: meter.progress(done/total, text=f'描画済み {done}/{total}ページ'))
        for message in messages: st.error(message)
    st.subheader('2. 制作完了チェック')
    rows = [{'ページ': s['page'], '自動検査': '検査済み' if s['scan_done'] else '未検査 / 要確認', '目視プレビュー': '確認済み' if s['preview'] else '未確認',
             '手動レビュー': '確認済み' if s['manual_done'] else '要再確認' if s['manual_stale'] else '未完了', '未解決タスク': s['tasks'], '最終確認': '完了' if s['final'] else '未完了'} for s in overview['states']]
    if rows: st.dataframe(rows, hide_index=True, use_container_width=True)
    pending = next((s['page'] for s in overview['states'] if not s['final']), None)
    if pending:
        st.info(f'次に確認するページ：{pending}ページ。ページ一覧でプレビュー・手動レビュー・最終確認を行ってください。')
        if st.button('次の未確認ページを開く'):
            st.session_state[f'{prefix}_jump'] = dict(page=pending); st.rerun()
    notes = export_warnings(context, data)
    for note in notes: st.warning(note)
    st.caption('未確認・主観的なAI指摘だけで出力は禁止しません。文字・フォント等の出力不能エラーは従来どおり書き出し時に停止します。')
    acknowledged = st.checkbox('未確認項目の警告を確認して書き出しへ進む', key=f'{prefix}_export_ack_{context.snapshot["token"]}', value=not notes)
    if st.button('PNG・PDF・縦読み書き出しへ', disabled=not acknowledged): navigate('完成・書き出し', 'PNG・PDF書き出し')
