"""保存した検査の更新検知、問題一覧、作画指示の対象コマへの移動。"""
from manga.character_ai_ui import action
from manga.ui_runtime import ui as st


def checks(service, data, prefix, navigate):
    st.subheader('テキスト・版・参照の一貫性チェック')
    st.caption('自然文に書かれた外見を網羅的には理解しません。「悠真：髪色=黒、服装=制服」等の明示ラベルと、下のコマ別指定を検査します。')
    if st.button('静的一貫性チェックを実行', type='primary'):
        action(lambda:service.run_checks(expected_revision=data['revision']), '静的検査を保存しました。実画像の見た目は人間が確認してください。')
    if data['checks']:
        check = data['checks'][-1]
        stale = check['source_token'] != service.snapshot()['token']
        st.write(f"保存検査：{check['pages']}ページ・{check['panels']}コマ / 指摘{len(check['issues'])}件 / {'古い検査・再実行が必要' if stale else '設定の静的検査済み（画像の見た目は未検査）'}")
        if stale: st.warning('検査後に設定・脚本・参照・画像等が変わりました。以前の指摘を残し、再検査してください。')
        issues = check['issues']
        if issues:
            st.dataframe([dict(ページ=i['page'], コマ=i['panel'], 種類=i['kind'], 区分=i['severity'], 指摘=i['message']) for i in issues], hide_index=True, use_container_width=True)
            selected = st.selectbox('詳しく確認する指摘', list(range(len(issues))), format_func=lambda n:f"{issues[n]['page'] or '全体'} / {issues[n]['panel'] or '-'}：{issues[n]['message']}", key=prefix+'_issue')
            issue = issues[selected]
            with st.expander('設定・根拠・期待値の詳細'): st.json(issue, expanded=False)
            if navigate and issue['page'] and issue['panel'] and st.button('このコマの作画指示・画像を確認する'):
                from manga.panel_workspace_ui import open_panel
                open_panel(service.project_id, issue['page'], issue['panel'], navigate)
        else: st.info('今回の静的検査では指摘0件です。画像の外見と自然文の意味は未検査です。')
    else: st.info('未検査です。問題なしとは判断していません。')
    with st.expander('コマごとの人物設定を記録する（任意）'):
        art = service.artwork.load()
        targets = {key:entry for key,entry in art['panels'].items() if entry['direction']}
        if not targets: st.info('先に作画指示を登録してください。'); return
        target = st.selectbox('確認するコマ', list(targets), key=prefix+'_observation_target')
        entry = targets[target]
        people = {c['id']:c for c in art['characters'] if not c['deleted_at'] and c['id'] in entry['direction']['character_ids']}
        if not people: st.info('このコマには登場人物IDが未設定です。'); return
        identifier = st.selectbox('コマ設定の人物', list(people), format_func=lambda i:people[i]['name']+' / '+i[:8], key=prefix+'_'+target+'_observation_person')
        from manga.character_ai_models import FIXED_FIELDS
        latest = service.proposals.latest()
        previous = next((r for r in reversed(data['observations']) if (r['story_version_id'],r['page'],r['panel'],r['character_id']) == (latest['id'],entry['page'],entry['panel'],identifier)), None)
        key = prefix+'_'+target+'_'+identifier+'_observation_'+str(previous['id'] if previous else 'new')
        selected = st.multiselect('このコマで明示する特徴', list(FIXED_FIELDS), default=list(previous['fields']) if previous else [], format_func=lambda k:FIXED_FIELDS[k], key=key+'_fields')
        values = {k:st.text_input('コマ設定：'+FIXED_FIELDS[k], value=previous['fields'].get(k,'') if previous else '', max_chars=2000, key=key+'_'+k) for k in selected}
        reason = st.text_input('コマ設定の記述根拠・確認事項', max_chars=2000, key=key+'_reason')
        st.caption('この指定は検査用の記録です。矛盾があれば自動上書きせず、作画指示またはバイブルの例外を修正します。空の特徴一覧で保存すると現在版の明示指定を解除できます。')
        if st.button('コマ別設定を履歴へ保存', disabled=not reason.strip()):
            action(lambda:service.save_observation(entry['page'], entry['panel'], identifier, values, reason, version_id=latest['id'], expected_revision=data['revision'], expected_artwork_revision=art['revision']), 'コマ別設定を記録しました。静的検査を再実行してください。')
