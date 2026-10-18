"""参考技法→概要3案→詳細脚本→PDCA→確定を、選択工程だけ描画する。"""
from manga.ui_runtime import ui as st
from manga.ui_data import read_store
from manga.storage import StorageError
from manga.story_studio import StudioService
from manga.story_studio_status import stage, selected_concept
from manga.story_techniques import technique_options, stale_references
from manga.story_studio_ui_common import STEPS, act, move, request_picker


def render_story_studio(project, directory, navigate=None, initial_step=None):
    st.header('ストーリー制作スタジオ')
    st.caption('概要3案 → 選択 → 詳細脚本 → 評価 → 改善 → 再評価 → 人間による最終確定。ChatGPTへの送信は手動です。')
    service = StudioService(directory, project.id)
    notice_key = f'story_studio_notice_{project.id}'
    if notice_key in st.session_state:
        notice = st.session_state.pop(notice_key)
        st.success(notice.get('message', '') if isinstance(notice, dict) else notice)
    try:
        pending = service.transactions.pending()
        if pending:
            st.error('前回の制作保存が途中です。他画面で編集せず、保存を再開してください。')
            if st.button('制作保存を再開', type='primary'):
                act(service, service.transactions.resume, '中断した保存を再開しました。')
            return
        data = read_store(service.concepts)
        history = read_store(service.stories)
        editorial = read_store(service.editorial)
    except (StorageError, ValueError, OSError) as error:
        st.error(str(error)); return
    next_step, message = stage(data, editorial, history)
    st.subheader('次にやること：'+message)
    st.caption(f"現在の制作段階：{next_step} ｜ 保存済み脚本：{len(history)}版 ｜ 概要の提案：{len(data['batches'])}回")
    if st.button('次の工程を開く', type='primary'):
        move(project.id, next_step)
    key = f'story_studio_step_{project.id}'
    pending_key = f'story_studio_next_{project.id}'
    entry_key = f'story_studio_entry_{project.id}'
    entry = initial_step or 'studio'
    if st.session_state.get(entry_key)!=entry:
        st.session_state[key]=initial_step or next_step
        st.session_state[entry_key]=entry
    if pending_key in st.session_state:
        st.session_state[key] = st.session_state.pop(pending_key)
    step = st.radio('制作ステップ', STEPS, index=0, horizontal=True, key=key)
    prefix = f'story_studio_{project.id}'
    # 工程が切り替わる再実行でも、旧工程の入力ウィジェットを残さない。
    with st.empty().container():
        if step == '参考作品・技法':
            references_view(service, data, editorial, prefix, notice_key)
        elif step == '概要3案・選択':
            concepts_view(service, data, editorial, history, prefix)
        elif step == '詳細脚本':
            detail_view(service, data, editorial, history, prefix)
        elif step == '評価・改善':
            from manga.story_pdca_ui import pdca_view
            pdca_view(service, data, editorial, history, prefix)
        else:
            from manga.story_pdca_ui import comparison_view
            comparison_view(service, data, editorial, history, prefix, navigate)


def references_view(service, data, editorial, prefix, notice_key):
    view = st.radio('参考作品の作業', ('登録・編集', '分析', '使用技法の選択'), horizontal=True, key=f'{prefix}_references_view')
    from manga.reference_ui import reference_management, reference_analysis
    if view == '登録・編集':
        reference_management(service.editorial, editorial, prefix+'_refs', notice_key)
    elif view == '分析':
        reference_analysis(service.editorial, editorial, prefix+'_refs', notice_key)
    else:
        active = {r['id']: r for r in editorial['references'] if not r['deleted_at']}
        saved = data.get('reference_choices', {})
        ids = st.multiselect('概要・詳細・評価・改善で使う参考作品（最大3作品・任意）', list(active),
                             default=[k for k in saved if k in active], format_func=lambda k: active[k]['title'],
                             max_selections=3, key=f'{prefix}_ref_ids')
        options, warnings = technique_options(editorial, ids)
        for warning in warnings:st.warning(warning)
        choices = {}
        for ref in options:
            techniques = {t['id']: t for t in ref['techniques']}
            choices[ref['reference_id']] = st.multiselect(ref['title']+'：使用する抽象技法', list(techniques),
                default=[k for k in saved.get(ref['reference_id'], list(techniques)[:1]) if k in techniques],
                format_func=lambda k, techniques=techniques: techniques[k]['text']+'（'+techniques[k]['status']+'）',
                key=f"{prefix}_{ref['reference_id']}_{ref['analysis_id']}_techniques")
        st.caption('未確認・不明の分析は選択肢に含めません。登録メモは未確認として明記します。参考作品なしでも制作できます。')
        if st.button('使用する参考技法を保存'):
            act(service, lambda: service.save_choices(choices, expected_revision=data['revision']), '使用技法を保存しました。新しい概要依頼に反映します。', '概要3案・選択')


def concepts_view(service, data, editorial, history, prefix):
    st.subheader('ストーリー概要案作成：3案を比較して1案を採用')
    st.caption('中心的な葛藤・転換点・結末の方向性が異なる3案を一度に依頼します。意味上の独自性・差別化は人間が確認してください。')
    if st.button('概要3案の依頼を作成', type='primary'):
        act(service, lambda: service.create_request('concepts', choices=data.get('reference_choices', {}), expected_revision=data['revision']), '概要3案の依頼を作成しました。')
    if data['batches']:
        batches = {b['id']: b for b in reversed(data['batches'])}
        batch_id = st.selectbox('比較する概要3案', list(batches), format_func=lambda k:batches[k]['saved_at'], key=f"{prefix}_{data['batches'][-1]['id']}_batch")
        batch = batches[batch_id]
        source = next(r for r in data['requests'] if r['id'] == batch['request_id'])
        for warning in stale_references(source['context']['references'], editorial):st.warning(warning)
        selection, _, _ = selected_concept(data)
        for column, concept in zip(st.columns(3), batch['payload']['concepts']):
            with column:
                st.markdown('### '+concept['title'])
                st.write(concept['logline'])
                st.write(concept['synopsis'])
                st.write('葛藤：'+concept['conflict'])
                st.write('転換点：'+concept['turning_point'])
                st.write('結末：'+concept['ending_direction'])
                with st.expander('人物・技法・独自性・懸念点'):
                    st.json({k:v for k,v in concept.items() if k not in ('title','logline','synopsis','conflict','turning_point','ending_direction')}, expanded=False)
                adopted = bool(selection and selection['batch_id'] == batch_id and selection['concept_id'] == concept['id'])
                if adopted:st.success('採用中')
                if st.button('この概要案を採用', key=f"{prefix}_{batch_id}_{concept['id']}_select", disabled=adopted, type='primary'):
                    act(service, lambda: service.select(batch_id, concept['id'], expected_revision=data['revision']), '概要案を明示採用しました。不採用案も履歴に保持しています。', '詳細脚本')
        st.caption('別の3案を生成するには「概要3案の依頼を作成」を再度押します。既存候補・採用履歴は削除しません。')
    request_picker(service, data, history, ('concepts',), prefix+'_concepts')


def detail_view(service, data, editorial, history, prefix):
    selection, concept, source = selected_concept(data)
    if not selection:
        st.info('概要3案を取り込み、1案を採用してください。')
        if st.button('概要3案へ'):move(service.project_id, '概要3案・選択')
        return
    st.subheader('採用概要から詳細脚本を作成：'+concept['title'])
    st.write(concept['synopsis'])
    st.caption(f"概要候補ID：{concept['id']} ｜ 採用記録：{selection['id']}")
    for warning in stale_references(source['context']['references'], editorial):st.warning(warning)
    with st.expander('引き継ぐ概要・参考技法'):
        st.json(dict(concept=concept, references=source['context']['references']), expanded=False)
    if st.button('採用概要から詳細脚本の依頼を作成', type='primary'):
        act(service, lambda: service.create_request('detail', expected_revision=data['revision']), '詳細脚本の依頼を作成しました。')
    request_picker(service, data, history, ('detail',), prefix+'_detail')
