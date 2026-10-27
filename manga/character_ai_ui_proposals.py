"""人物を一人ずつ選び、現在値・提案・根拠を見て部分採用する。"""
from manga.artwork_models import CHARACTER_ALL_FIELDS
from manga.assistant_changes import differences, display_differences
from manga.character_ai_ui import action
from manga.ui_runtime import ui as st
from manga.voice_models import digest


def extraction(service, project, version_id, enabled, prefix,navigate=None):
    st.subheader('1. 確定脚本から人物候補を抽出')
    st.caption('ナレーターは人物へ自動登録しません。描写だけに登場する未登録人物は、名前を追加指定して確認できます。')
    names = st.text_area('追加して確認する人物名（任意・1行1名、脚本に記述が必要）', key=prefix+'_names', max_chars=5000)
    new = st.checkbox('同条件でも新しい人物提案を作る（旧提案を保持）', key=prefix+'_new')
    mode=st.radio('人物設定案の生成方法',('無料モック','Gemini 3.5 Flash-Lite'),key=prefix+'_method',horizontal=True)
    if mode=='Gemini 3.5 Flash-Lite':
        from manga.character_ai_ui_generation import generation
        generation(service,project,version_id,enabled,prefix,[n.strip() for n in names.splitlines() if n.strip()],new,navigate)
        return
    if st.button('無料モックで人物候補・設定案を生成', type='primary', disabled=not enabled):
        action(lambda:service.proposals.create_mock(project, version_id, expected_revision=service.proposals.load()['revision'], additional_names=[n.strip() for n in names.splitlines() if n.strip()], new_request=new), '人物提案を保存しました。「候補確認・採用」で同定と項目を確認してください。正式人物はまだ変更していません。')
    requests = [r for r in service.proposals.load()['requests'] if r.get('character_ai',{}).get('provider')=='mock' and r['story_version_id'] == version_id]
    if not requests: st.info('未作成です。上のボタンから開始してください。'); return
    request = requests[-1]
    extraction = request['character_ai']['extraction']
    st.caption('依頼ID：'+request['id']+(' / モック応答・API費用0円' if request['character_ai']['provider']=='mock' else ' / Geminiの履歴（実費は共有予算で確認）'))
    st.write(extraction['limitations'])
    st.dataframe([dict(候補=c['name'], 既存人物の照合候補=' / '.join(m['name']+' / '+m['character_id'][:8] for m in c['matches']) or '未同定', 根拠数=len(c['sources']), 状態='確認待ち') for c in extraction['candidates']], hide_index=True)
    if not extraction['candidates']: st.warning('人物候補は0件です。人物のない作品か、名前・話者が不明かを確認してください。')
    if extraction['unknown_speakers']:
        st.warning(f"不明・曖昧な話者が{len(extraction['unknown_speakers'])}件あります。勝手に人物へ割り当てていません。")
        with st.expander('不明話者の原文と対象コマ'): st.json(extraction['unknown_speakers'], expanded=False)
    st.caption(f"ナレーション：{len(extraction['narrator']['sources'])}コマ。ナレーターは音声工程の既存IDで扱います。")


def review(service, version_id, enabled, prefix):
    st.subheader('STEP 4：人物の同定・設定差分を確認して採用')
    data = service.proposals.load()
    requests = {r['id']:r for r in data['requests'] if r.get('character_ai') and r['story_version_id'] == version_id}
    proposals = {p['id']:p for p in reversed(data['proposals']) if p['request_id'] in requests}
    if not proposals: st.info('先に人物候補を抽出してください。0件の場合は話者や追加名を確認します。'); return
    proposal_id = st.selectbox('確認する人物提案', list(proposals), format_func=lambda i:proposals[i]['saved_at']+' / '+i[:8], key=prefix+'_proposal')
    proposal = proposals[proposal_id]
    items = {i['id']:i for i in proposal['payload']['items']}
    item_id = st.selectbox('確認する人物候補', list(items), format_func=lambda i:items[i]['fields']['name']+' / '+proposal['decisions'][i], key=prefix+'_'+proposal_id+'_item')
    item = items[item_id]
    key = prefix+'_'+proposal_id+'_'+item_id
    st.caption('状態：'+proposal['decisions'][item_id])
    request = requests[proposal['request_id']]
    st.caption('生成方式：'+('無料モック（実モデルの品質ではありません）' if request['character_ai']['provider']=='mock' else 'Gemini 3.5（人間の確認・採用前）'))
    original = next((c for c in request['character_ai']['extraction']['candidates'] if c['id'] == item_id), dict(matches=[], sources=[]))
    if not original['sources']: st.warning('取り込み候補に対応する抽出記録がありません。原文の根拠と人物の同定を人間が確認してください。')
    if original['matches']:
        st.warning('照合は候補です。人物を自動同定しません：'+' / '.join(m['name']+' / '+m['character_id'] for m in original['matches']))
    with st.expander('抽出元のページ・コマ・原文'): st.json(original['sources'], expanded=False)
    readonly = proposal['decisions'][item_id] != '確認待ち'
    if readonly:
        st.info('この提案は採用済みまたは見送りです。以下は閲覧専用です。原案・編集・採用履歴を保持しています。')
        adopted = [c for a in data['adoptions'] if a['proposal_id'] == proposal_id for c in a['changes'] if c['item_id'] == item_id]
        if adopted: st.caption('採用先の正式人物UUID：'+adopted[-1]['result_id'])
    _review_item(service, data, proposal_id, item_id, item, request, key, enabled, readonly)
    if readonly and proposal['decisions'][item_id]=='採用済み' and st.button('STEP 5：採用人物のバイブルを確認する',disabled=not enabled):
        from manga.character_ai_ui_generation import go_stage
        if adopted:st.session_state[prefix+'_bible_person']=adopted[-1]['result_id']
        go_stage(prefix,'キャラクターバイブル')


def _review_item(service, data, proposal_id, item_id, item, request, key, enabled, readonly):
    art = service.artwork.load()
    editorial = service.proposals.editorial.load()
    character_data = service.store.load()
    people = {c['id']:c for c in art['characters'] if not c['deleted_at'] and c['id'] in {p['id'] for p in request['context']['artwork']['characters']}}
    target = st.selectbox('同定した反映先（自動選択しません）', [None, *people], format_func=lambda i:people[i]['name']+' / '+i if i else '新規人物として登録', key=key+'_target', disabled=readonly)
    before = {k:people[target].get(k, '') for k in CHARACTER_ALL_FIELDS} if target else {}
    st.dataframe([dict(項目=label, 現在値=before.get(k, ''), 提案値=item['fields'].get(k, '') or '未設定', 区分=item['provenance'].get(k, {}).get('status', '要確認'), 根拠=item['provenance'].get(k, {}).get('evidence', '未指定')) for k,label in CHARACTER_ALL_FIELDS.items()], hide_index=True, use_container_width=True)
    st.caption('「視覚的な差別化」には作画時に維持する特徴を整理します。空欄は未設定です。AI提案は脚本の事実ではありません。')
    default = ['visual_identity'] if target and item['fields'].get('visual_identity') else [k for k,v in item['fields'].items() if v] if not target else []
    selected = st.multiselect('採用する項目（選択外の既存値は保持）', list(CHARACTER_ALL_FIELDS), default=default, format_func=lambda k:CHARACTER_ALL_FIELDS[k], key=key+'_'+str(target)+'_fields', disabled=readonly)
    values = dict(item['fields'])
    with st.expander('選択項目を修正（任意）'):
        for field in selected:
            values[field] = st.text_area('採用値：'+CHARACTER_ALL_FIELDS[field], value=values.get(field, ''), max_chars=100 if field == 'name' else 2000, key=key+'_'+str(target)+'_'+field, disabled=readonly)
    with st.expander('今回採用する実差分'):
        st.table(display_differences(differences({k:before.get(k, '') for k in selected}, {k:values[k] for k in selected})))
    from manga.character_ai_extraction import feature_matches
    for match in feature_matches(values,art['characters'],exclude=target):
        st.warning('特徴が似ている既存人物：'+match['name']+'（'+ '・'.join(CHARACTER_ALL_FIELDS[k] for k in match['fields'])+'）。別人かどうか確認してください。自動同定しません。')
    edited=any(values[k]!=item['fields'].get(k,'') for k in selected)
    if not readonly:st.caption('修正案は未保存です。修正案の保存、または正式採用で保存できます。' if edited else '提案は保存済みです。正式人物への反映には下の採用確認が必要です。')
    if st.button('修正案だけを保存（正式採用しない）',disabled=readonly or not enabled or not edited):
        def save_edit():
            import copy
            changed=copy.deepcopy(item)
            for field in selected:
                if values[field]!=changed['fields'].get(field,''):
                    changed['fields'][field]=values[field]
                    changed['provenance'][field]=dict(status='ユーザー編集',evidence='人物候補の修正案として明示保存')
            service.proposals.edit_item(proposal_id,item_id,changed,expected_revision=data['revision'])
        action(save_edit,'修正案を保存しました。正式人物は変更していません。採用する項目を確認してください。')
    for question in item['questions']: st.caption('確認事項：'+question)
    distinct = st.checkbox('同名・類似名の既存候補とは別人であることを確認した', key=key+'_distinct', disabled=readonly)
    consent = digest(dict(target=target, selected=selected, values={k:values[k] for k in selected}, distinct=distinct, revision=data['revision'], art=art['revision'], editorial=editorial['revision'], bible=character_data['revision']))
    confirmed = st.checkbox('人物の同定・原文・提案と事実の区別・選択項目を確認した', key=key+'_'+consent+'_confirmed', disabled=readonly)
    if st.button('この人物の選択項目だけを正式採用', type='primary', disabled=readonly or not enabled or not confirmed or not selected):
        action(lambda:service.proposals.adopt_fields(proposal_id, item_id, values, selected, target, expected_revision=data['revision'], expected_artwork_revision=art['revision'], expected_editorial_revision=editorial['revision'], expected_character_revision=character_data['revision'], identity_confirmed=confirmed, distinct_confirmed=distinct), '選択項目だけを採用しました。UUID・参照画像を保持しています。次にバイブルを確認してください。')
    if st.button('この人物提案を見送る', disabled=readonly):
        action(lambda:service.proposals.decide(proposal_id, [item_id], '見送り', expected_revision=data['revision']), '提案を見送りました。履歴は保持しています。')
