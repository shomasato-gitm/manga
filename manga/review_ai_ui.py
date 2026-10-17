"""ChatGPTへ画像を手動添付するための資料作成と、未確認提案の取り込み。"""
import json
from manga.ui_runtime import ui as st, operation
from manga.review_prompts import new_request, review_prompt, response_example
from manga.review_assets import review_package
from manga.review_ui_common import perform, issue_label, issue_rows
from manga.prompt_ui import prompt_tools
from manga.editorial_ui_common import json_input, input_text
from manga.gallery_ui import page_items
from manga.storage import StorageError


def render_ai_review(project, context, store, data, prefix):
    st.subheader('完成画像・完成ページのChatGPTレビュー（任意）')
    st.warning('AIの指摘は主観的な「未確認の提案」です。画像の品質を保証しません。画像・設定を自動修正せず、APIも使用しません。')
    if not context.latest or not context.artwork or not context.composition or context.errors:
        st.info('先にストーリー・作画設定・ページ編集の読込エラーを解消してください。'); return
    method = st.radio('レビュー資料の対象', ('ページ', '指定した複数コマ'), horizontal=True, key=f'{prefix}_ai_scope')
    targets = []
    if method == 'ページ':
        pages = st.multiselect('ChatGPTで確認するページ（必須）', [p['page'] for p in context.pages], default=[context.pages[0]['page']], key=f'{prefix}_ai_pages')
        targets = [dict(page=p, panel=None) for p in pages]
    else:
        options = [f'{p["page"]}:{c["panel"]}' for p in context.pages for c in p['panels']]
        selected = st.multiselect('比較する採用画像のコマ（必須・最大24コマ）', options, format_func=lambda key: key.replace(':','ページ・')+'コマ', key=f'{prefix}_ai_panels')
        targets = [dict(page=int(key.split(':')[0]), panel=int(key.split(':')[1])) for key in selected]
    include_pages = st.checkbox('比較対象コマの完成ページPNGも資料に含める', value=False, disabled=method == 'ページ', key=f'{prefix}_ai_include_pages')
    refs = {i['id']: f'{c["name"]} / {i["view"]} / {i["id"][:8]}' for c in context.artwork['characters'] if not c['deleted_at'] for i in c['references']}
    reference_ids = st.multiselect('添付するキャラクター基準・参照画像（任意）', list(refs), format_func=refs.get, key=f'{prefix}_ai_refs')
    question = st.text_area('期待する演出・特に確認したい観点（任意）', max_chars=4000, key=f'{prefix}_ai_question')
    if st.button('画像レビューの依頼を作成', type='primary'):
        def action():
            request = new_request(context, targets, reference_ids, question, include_pages=include_pages)
            request['prompt'] = review_prompt(project, context, request)
            store.save_request(request, expected_revision=data['revision'])
            st.session_state[f'{prefix}_ai_request_pending'] = request['id']
        perform(action, '画像レビューの依頼を保存しました。')
    if data['ai_requests']:
        pending = st.session_state.pop(f'{prefix}_ai_request_pending', None)
        requests = list(reversed(data['ai_requests']))
        # 依頼内容は選んだ1件だけ描画。以前の依頼も表示範囲を変えて確認できる。
        if pending:
            st.session_state[f'{prefix}_ai_request_batch'] = 0
            st.session_state[f'{prefix}_ai_request'] = pending
        shown = page_items(requests, f'{prefix}_ai_request_batch', '画像レビュー依頼', size=10)
        by_id = {r['id']: r for r in shown}
        if st.session_state.get(f'{prefix}_ai_request') not in by_id: st.session_state[f'{prefix}_ai_request'] = shown[0]['id']
        selected = st.selectbox('取得する画像レビュー依頼', list(by_id), format_func=lambda i, records=by_id: f'{records[i]["created_at"]} / {i[:8]}', key=f'{prefix}_ai_request')
        request = by_id[selected]
        current = request['snapshot']['token'] == context.snapshot['token']
        prompt = request.get('prompt') or (review_prompt(project, context, request) if current else '')
        if prompt: prompt_tools(prompt, f'{prefix}_{selected}', 'image_review_prompt.txt', request=request)
        st.download_button('回答用JSON形式をダウンロード', json.dumps(response_example(request), ensure_ascii=False, indent=2), file_name='image_review_response.json', mime='application/json', key=f'{prefix}_ai_schema')
        if not current: st.warning('依頼後に制作データが変わりました。履歴は保持しています。資料の再生成・返答取り込みには新しい依頼が必要です。')
        if st.button('ChatGPTに渡すレビュー資料ZIPを生成', disabled=not current):
            try:
                with operation('選択した画像をローカルで資料にまとめています'):
                    content, notes = review_package(context, request, prompt)
                # セッションに保持する大きな資料は最後の1件だけ。
                st.session_state['_review_package'] = dict(project_id=project.id, request_id=selected, token=context.snapshot['token'], content=content, notes=notes)
            except (ValueError, OSError, StorageError) as error: st.error(str(error))
        package = st.session_state.get('_review_package', {})
        if current and package.get('project_id') == project.id and package.get('request_id') == selected and package.get('token') == context.snapshot['token']:
            for note in package['notes']: st.warning(note)
            st.download_button('レビュー資料ZIPをダウンロード', package['content'], file_name='image_review_materials.zip', mime='application/zip')
            st.info('ZIPを展開し、PNG・比較画像・選んだ参照画像をChatGPTに手動添付してください。プロンプトを送り、返答JSONを下で取り込みます。')
    st.subheader('ChatGPTからの画像レビューJSONを取り込む')
    values = json_input('画像レビューJSON', f'{prefix}_ai_import')
    if st.button('画像レビューJSONを検証・保存'):
        perform(lambda: store.import_ai(input_text(values), expected_revision=data['revision'], snapshot=context.snapshot), 'AIの指摘を未確認の提案として保存しました。')
    if data['ai_reviews']:
        reviews = page_items(list(reversed(data['ai_reviews'])), f'{prefix}_ai_review_batch', 'AI画像レビュー結果', size=5)
        by_id = {r['id']:r for r in reviews}
        selected = st.selectbox('確認するAI画像レビュー', list(by_id), format_func=lambda i, records=by_id: f'{records[i]["created_at"]} / {i[:8]}', key=f'{prefix}_ai_review')
        review = by_id[selected]
        request = next(r for r in data['ai_requests'] if r['id'] == review['request_id'])
        if request['snapshot']['token'] != context.snapshot['token']: st.warning('この指摘は変更前の画像・設定に対する提案です。現在の内容を確認して判断してください。')
        st.caption('状態：' + ('ユーザーが結果を確認済み' if review['reviewed'] else '未確認の提案'))
        if review['issues']:
            st.dataframe(issue_rows(review['issues']), hide_index=True, use_container_width=True)
            issue = st.selectbox('判断するAI指摘', review['issues'], format_func=issue_label, key=f'{prefix}_ai_issue_{selected}')
            st.write('根拠：' + issue['reason']); st.write('改善案：' + issue['suggestion'])
            st.write('人間確認：' + ('AIも確認を要請' if issue['needs_human_check'] else 'AIの指定にかかわらず人間が最終判断'))
            st.caption('採否：' + review['decisions'][issue['id']])
            left, right = st.columns(2)
            if left.button('このAI指摘を確認して修正タスクにする'):
                perform(lambda: store.create_task(issue, origin='ai', source_id=selected, expected_revision=data['revision'], snapshot=context.snapshot), 'AI提案から修正タスクを登録しました。')
            if right.button('このAI指摘は対応不要と判断する'):
                perform(lambda: store.decide_ai(selected, issue['id'], expected_revision=data['revision'], snapshot=context.snapshot), 'AI指摘を対応不要として記録しました。')
        else: st.info('AIが報告した問題は0件です。これだけでページを合格にはしません。目視レビューを行ってください。')
        if st.button('このAIレビュー結果を読んで確認した'):
            perform(lambda: store.decide_ai(selected, expected_revision=data['revision'], snapshot=context.snapshot), 'AIレビュー結果の確認を保存しました。')
        with st.expander('AI画像レビューのJSON詳細'): st.json(review, expanded=False)
