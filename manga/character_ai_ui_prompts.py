"""統一プロンプトの明示作成・コピー・保存と、選択した履歴だけの閲覧。"""
import json

from manga.character_ai_ui import action
from manga.ui_runtime import ui as st
from manga.voice_models import digest


def prompts(service, data, enabled, prefix, navigate):
    art = service.artwork.load()
    kind = st.radio('画像プロンプトの対象', ('character', 'panel'), format_func=lambda k:'人物の基準画像' if k=='character' else 'ページ・コマの画像', horizontal=True, key=prefix+'_prompt_kind')
    values = {}
    if kind == 'character':
        people = {c['id']:c for c in art['characters'] if not c['deleted_at']}
        if not people: st.info('正式な人物を先に登録してください。'); return
        values['character_id'] = st.selectbox('画像プロンプトの人物', list(people), format_func=lambda i:people[i]['name']+' / '+i[:8], key=prefix+'_prompt_person')
        values['view'] = st.selectbox('基準画像の種類', ['正面','斜め','全身','表情差分'], key=prefix+'_prompt_view')
        values['expression'] = st.text_input('表情（任意）', value='自然な表情', max_chars=500, key=prefix+'_prompt_expression')
    else:
        pages = service.proposals.latest()['story']['pages']
        values['page'] = st.selectbox('プロンプトのページ', [p['page'] for p in pages], key=prefix+'_prompt_page')
        values['panel'] = st.selectbox('プロンプトのコマ', [c['panel'] for c in pages[values['page']-1]['panels']], key=prefix+'_'+str(values['page'])+'_prompt_panel')
    selection = digest(dict(kind=kind, values=values))
    if st.button('確認済み設定で画像プロンプトを作成', type='primary', disabled=not enabled):
        def build(): st.session_state[prefix+'_prompt_package'] = dict(selection=selection, package=service.prompt(kind, **values))
        action(build, '画像プロンプトを作成しました。参照画像は手動で添付してください。')
    saved = st.session_state.get(prefix+'_prompt_package')
    if not saved or saved['selection'] != selection: return
    package = saved['package']
    stale = package['source_token'] != service.snapshot()['token']
    if stale: st.warning('作成後に設定が変わりました。保存前にプロンプトを作成し直してください。')
    for warning in package['warnings']: st.warning(warning)
    st.caption('正式脚本版：'+package['story_version_id']+' / 確認済みバイブル：'+', '.join(i[:8] for i in package['bible_ids']))
    from manga.prompt_ui import prompt_tools
    prompt_tools(package['prompt'], prefix+'_unified_prompt', 'character_consistent_prompt.txt', response_json=False,
                 next_step='必要な参照画像をChatGPTへ手動添付して生成してください。画像は従来の基準画像登録またはコマ制作画面から手動で登録・採用します。')
    if package['reference_images']:
        with st.expander('添付する参照画像のID・ハッシュ・欠損状態'): st.json(package['reference_images'], expanded=False)
    if st.button('このプロンプトを制作履歴へ保存', disabled=stale): action(lambda:service.save_prompt(package, expected_revision=data['revision']), 'プロンプトを履歴へ保存しました。画像は自動生成・採用していません。')
    if navigate:
        if kind=='character' and st.button('基準画像を手動登録する'): navigate('キャラクター・作風', '基準画像・作風')
        if kind=='panel' and st.button('このコマの画像を手動登録する'):
            from manga.panel_workspace_ui import open_panel
            open_panel(service.project_id, values['page'], values['panel'], navigate)


def history_view(service, data, prefix):
    st.subheader('バイブル・検査・プロンプト履歴')
    for collection, label in [('bibles','バイブル'),('checks','静的検査'),('prompts','画像プロンプト')]:
        rows = {r['id']:r for r in reversed(data[collection])}
        with st.expander(label+f'の履歴（{len(rows)}件）'):
            if not rows: st.caption('未作成です。'); continue
            identifier = st.selectbox(label+'の履歴を選択', list(rows), format_func=lambda i:rows[i]['saved_at']+' / '+i[:8], key=prefix+'_'+collection+'_history')
            row = rows[identifier]
            st.json(row, expanded=False)
            st.download_button(label+'履歴JSONを取得', json.dumps(row, ensure_ascii=False, indent=2), file_name=collection+'_'+identifier+'.json', mime='application/json')
    with st.expander('人物提案・同定・部分採用の履歴'):
        assistant = service.proposals.load()
        adoptions = [a for a in assistant['adoptions'] if a.get('character_ai')]
        st.dataframe([dict(採用ID=a['id'], 保存日時=a['saved_at'], 採用項目=' / '.join(a['character_ai']['selected_fields']), 人物UUID=a['changes'][0]['result_id']) for a in adoptions], hide_index=True)
        st.caption('原案と編集履歴は既存assistant.jsonにも保持しています。')
