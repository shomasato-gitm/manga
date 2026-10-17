"""修正→再確認待ち→人間による確認完了。古いタスクも履歴として保持する。"""
from manga.ui_runtime import ui as st
from manga.review_models import TASK_STATES, CORRECTION_TARGETS
from manga.review_status import task_status, task_current
from manga.review_ui_common import perform, issue_label, open_issue
from manga.gallery_ui import page_items


def render_tasks(project, context, store, data, prefix, navigate):
    st.subheader('修正タスク')
    st.caption('修正したら「修正済み」または「再確認待ち」へ進め、実際の画像・ページを確認して「確認完了」にします。過去の指摘は消えません。')
    if not data['tasks']: st.info('自動チェック・目視レビュー・AI提案から、確認した問題をタスクに登録できます。'); return
    pending_filter = st.session_state.pop(f'{prefix}_task_filter_pending', None)
    if pending_filter: st.session_state[f'{prefix}_task_filter'] = pending_filter
    filter_value = st.radio('タスクの表示', ('未解決', 'すべて'), horizontal=True, key=f'{prefix}_task_filter')
    rows = [t for t in data['tasks'] if filter_value == 'すべて' or task_status(t, context.snapshot) not in ('確認完了', '対応不要')]
    st.dataframe([{'対象': issue_label(t['issue']), '重要度': t['issue']['severity'], '状態': task_status(t, context.snapshot), '発見元': {'auto':'自動検査', 'manual':'目視', 'ai':'AIの未確認提案'}[t['origin']], '発見後の変更': 'なし' if task_current(t, context.snapshot) else 'あり・再確認必要'} for t in rows], hide_index=True, use_container_width=True)
    if not rows: st.success('表示条件に合う未解決タスクはありません。'); return
    by_id = {t['id']:t for t in rows}
    selection_key = f'{prefix}_task'
    pending = st.session_state.pop(f'{prefix}_task_pending', None)
    if pending in by_id: st.session_state[selection_key] = pending
    if st.session_state.get(selection_key) not in by_id: st.session_state[selection_key] = rows[0]['id']
    selected = st.selectbox('確認・更新するタスク', list(by_id), format_func=lambda i: issue_label(by_id[i]['issue']), key=selection_key)
    task = by_id[selected]; issue = task['issue']; state = task_status(task, context.snapshot)
    st.markdown(f'**{issue_label(issue)}**')
    st.write(f'重要度：{issue["severity"]} ｜ 修正状態：{state} ｜ 修正対象：{CORRECTION_TARGETS[issue["target"]]}')
    st.write(f'判断理由：{issue["reason"]}'); st.write(f'改善案：{issue["suggestion"]}')
    if not task_current(task, context.snapshot): st.warning('発見・確認後に対象のストーリー、作画、画像、ページ編集が変わっています。過去の指摘を残し、現在の状態を再確認してください。')
    if st.button('このタスクの対象画面へ移動', type='primary'):
        from manga.review_models import target
        try: target(issue, context.pages, global_ok=True)
        except ValueError: st.warning('このページ・コマは現在の脚本にありません。履歴を確認し、必要なら「対応不要」にしてください。')
        else: open_issue(project.id, issue, navigate, task_id=task['id'])
    key = f'{prefix}_task_edit_{task["id"]}_{task["updated_at"]}_{context.snapshot["token"]}'
    method = st.text_area('修正方法・対応メモ', value=task['method'], max_chars=4000, key=f'{key}_method')
    status = st.selectbox('修正状態', TASK_STATES, index=TASK_STATES.index(state), key=f'{key}_status')
    checked = st.checkbox('現在の保存済み画像・設定を人間が再確認した', key=f'{key}_checked')
    if st.button('タスクの状態を保存'):
        # 完了後も選択を維持して履歴を確認できる。
        def action():
            store.update_task(task['id'], status, method, expected_revision=data['revision'], snapshot=context.snapshot, rechecked=checked)
            st.session_state[f'{prefix}_task_pending'] = task['id']
            st.session_state[f'{prefix}_task_filter_pending'] = 'すべて'
        perform(action, 'タスクの対応状態を保存しました。')
    with st.expander('発見時の識別情報・対応履歴'):
        st.json(task, expanded=False)
        events = [e for e in data['events'] if e.get('task_id') == task['id']]
        for event in page_items(list(reversed(events)), f'{prefix}_task_history', '対応履歴', size=5): st.json(event, expanded=False)
