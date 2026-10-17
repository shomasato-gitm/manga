"""縮小一覧から1ページだけを読み込み、目視確認を保存する。"""
from manga.ui_runtime import ui as st, operation
from manga.review_models import MANUAL_ITEMS, MANUAL_STATES
from manga.review_context import page_current
from manga.review_status import page_status
from manga.review_assets import read_thumbnail, page_preview, save_thumbnails
from manga.review_ui_common import perform, issue_rows, open_issue
from manga.review_checks import make_issue
from manga.artwork_storage import ArtworkStore
from manga.gallery_ui import thumbnail, page_items
from manga.page_render import adopted_image
from manga.storage import StorageError


def render_pages(project, context, store, data, prefix, navigate):
    if not context.pages: st.info('ストーリーを保存してからページを確認してください。'); return
    if not context.artwork or not context.composition:
        st.error('作画設定またはページ編集を読み込めません。自動チェックの読込エラーを確認してください。'); return
    st.subheader('全体を見て、確認するページを選ぶ')
    st.caption('一覧は180pxの縮小PNGのみです。現在の編集と一致するサムネイルがないページは「未作成」と表示します。')
    if st.button('一覧サムネイルを作成・更新'):
        meter = st.progress(0, text='ページを順番に描画します')
        with operation('ページの縮小プレビューを準備しています'):
            errors = save_thumbnails(context, progress=lambda done, total: meter.progress(done/total, text=f'描画済み {done}/{total}ページ'))
        for error in errors: st.error(error)
    shown = page_items(context.pages, f'{prefix}_thumbnail_batch', 'ページ一覧', size=12)
    columns = st.columns(4)
    for index, page in enumerate(shown):
        number = page['page']; state = page_status(data, context.snapshot, number)
        with columns[index % 4]:
            try: content = read_thumbnail(context, number)
            except (OSError, StorageError) as error: content = None; st.warning(str(error))
            if content: st.image(content, caption=f'{number}ページ', width=180)
            else: st.info(f'{number}ページ：サムネイル未作成')
            st.caption('最終確認済み' if state['final'] else '手動レビュー済み' if state['manual_done'] else '要確認')
            if st.button(f'{number}ページを確認', key=f'{prefix}_thumb_{number}'):
                st.session_state[f'{prefix}_jump'] = dict(page=number); st.rerun()
    number = st.selectbox('品質確認するページ', [p['page'] for p in context.pages], key=f'{prefix}_page')
    page = context.pages[number-1]
    state = page_status(data, context.snapshot, number)
    fp = context.snapshot['pages'][str(number)]['fingerprint']
    active_key = f'{prefix}_preview_active_{number}_{fp}'
    st.subheader(f'{number}ページの完成プレビュー')
    if st.button('選択ページをプレビュー', type='primary'): st.session_state[active_key] = True
    rendered = None
    if st.session_state.get(active_key):
        try:
            with operation('選択ページの保存済み内容を描画しています'):
                rendered = page_preview(context, number)
            st.image(rendered.png, caption='保存済みの配置・トリミング・本文。原寸出力を縮小表示しています。', use_container_width=False)
            for error in rendered.errors: st.error(error)
            for warning in rendered.warnings: st.warning(warning)
        except (ValueError, OSError, StorageError) as error: st.error(str(error))
    if state['preview']: st.success('現在の保存データのプレビューは目視確認済みです。')
    if st.button('このプレビューを目視確認した', disabled=rendered is None or bool(rendered.errors if rendered else True) or not state['scan_done']):
        perform(lambda: store.confirm_preview(number, expected_revision=data['revision'], snapshot=context.snapshot), 'プレビューの目視確認を保存しました。')
    st.caption('目視確認を記録する前に自動チェックを実行してください。サムネイル生成や描画成功だけでは目視確認済みにしません。')
    scan = state['scan']
    issues = [i for i in scan['issues'] if i['page'] in (None, number)] if scan else []
    if issues: st.dataframe(issue_rows(issues), use_container_width=True, hide_index=True)
    elif not scan: st.warning('このページは未検査、または保存データが変わって要再検査です。')
    with st.expander('各コマの脚本・セリフ・登場人物・採用画像'):
        cast = {c['id']: c['name'] for c in context.artwork['characters']}
        images = ArtworkStore(context.root.parent, project.id)
        for panel in page['panels']:
            c = panel['panel']; entry = context.artwork['panels'].get(f'{number}:{c}', {})
            st.markdown(f'**{c}コマ目**'); st.write(panel['description'])
            st.text(f'セリフ：{panel["dialogue"] or "なし"}\nナレーション：{panel["narration"] or "なし"}')
            st.caption('登場人物：' + '、'.join(cast.get(i, '不明な人物ID') for i in (entry.get('direction') or {}).get('character_ids', [])))
            image = adopted_image(context.artwork, number, c)
            if image:
                try: st.image(thumbnail(images, image), width=240)
                except (ValueError, OSError, StorageError) as error: st.warning(str(error))
            else: st.warning('画像未採用')
            if st.button(f'{number}ページ・{c}コマを画像制作で開く', key=f'{prefix}_panel_{number}_{c}'):
                open_issue(project.id, dict(page=number, panel=c, target='image'), navigate)
    _manual(project, context, store, data, prefix, number, state)
    st.subheader('このページの最終確認')
    if state['final']: st.success('現在の保存データについて最終確認済みです。')
    else: st.info('自動検査・プレビュー目視確認・手動7項目・修正タスクの再確認がそろったら最終確認を保存できます。')
    ready = state['scan_done'] and state['preview'] and state['manual_done'] and not state['blockers'] and not state['tasks']
    if st.button('このページを最終確認済みにする', disabled=not ready):
        perform(lambda: store.finalize(number, expected_revision=data['revision'], snapshot=context.snapshot), 'ページの最終確認を保存しました。')
    if st.button('このページを漫画ページ編集で開く'):
        open_issue(project.id, dict(page=number, panel=None, target='layout'), navigate)


def _manual(project, context, store, data, prefix, number, state):
    st.subheader('人間が画像を見て確認する項目（自動検証ではありません）')
    saved = data['manual'].get(str(number))
    if state['manual_stale']: st.warning('確認後にこのページの素材・設定が変わりました。以前の回答は保持しています。画像を再確認して保存してください。')
    stamp = saved['updated_at'] if saved else 'new'
    key = f'{prefix}_manual_{number}_{stamp}'
    answers = saved['answers'] if saved else {name: '未確認' for name in MANUAL_ITEMS}
    if st.button('目視した7項目をすべて「問題なし」に設定', key=f'{key}_bulk'):
        for name in MANUAL_ITEMS: st.session_state[f'{key}_{name}'] = '問題なし'
    values = {}
    for name, label in MANUAL_ITEMS.items():
        values[name] = st.selectbox(label, MANUAL_STATES, index=MANUAL_STATES.index(answers[name]), key=f'{key}_{name}')
    notes = st.text_area('目視レビューのメモ（任意）', value=saved['notes'] if saved else '', max_chars=4000, key=f'{key}_notes')
    if st.button('手動レビューを保存', type='primary'):
        perform(lambda: store.save_manual(number, values, notes, expected_revision=data['revision'], snapshot=context.snapshot), '手動レビューを保存しました。')
    with st.expander('目視で見つけた問題を修正タスクにする'):
        name = st.selectbox('問題のある確認項目', list(MANUAL_ITEMS), format_func=MANUAL_ITEMS.get, key=f'{key}_issue_kind')
        panel = st.selectbox('修正対象のコマ（ページ全体も選択可）', [None]+[p['panel'] for p in context.pages[number-1]['panels']], format_func=lambda c: f'{c}コマ目' if c else 'ページ全体', key=f'{key}_issue_panel')
        message = st.text_area('問題の内容（必須）', key=f'{key}_issue_text', max_chars=4000)
        severity = st.selectbox('目視指摘の重要度', ('高', '中', '低'), index=1, key=f'{key}_severity')
        correction = st.selectbox('修正するもの', ['image', 'direction', 'bubble', 'layout'], format_func=lambda v: {'image':'画像', 'direction':'作画指示', 'bubble':'吹き出し', 'layout':'レイアウト'}[v], key=f'{key}_target')
        if st.button('目視指摘をタスクに登録'):
            def action():
                if not message.strip(): raise ValueError('問題の内容を入力してください。')
                issue = make_issue(project.id, MANUAL_ITEMS[name], message, page=number, panel=panel, severity=severity, target=correction, reason='利用者が完成画像を目視確認しました。')
                store.create_task(issue, origin='manual', expected_revision=data['revision'], snapshot=context.snapshot)
            perform(action, '目視指摘の修正タスクを登録しました。')
