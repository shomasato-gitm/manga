"""同期画面の各作業。選んだ1画面だけを構築する。"""
import json
import unicodedata
from manga.ui_runtime import ui as st
from manga.sync_manifest import decode_json,sha256,json_bytes
from manga.sync_adoption import SCOPES
from manga.storage import ProjectStore
from manga.sync_outputs import output_index,read_output,current_output_fingerprint
from manga.sync_pdca import set_policy,record_iteration
from manga.sync_responses import fetch_response,accept_response
from manga.gallery_ui import page_items


def import_view(service,data,branch,prefix,perform):
    st.subheader('1. 取得方法を選ぶ')
    mode=st.radio('GitHubからの取得方式',('作品スナップショット','既存worksファイル','AI提案・画像レビューの回答'),key=f'{prefix}_import_mode')
    if mode=='作品スナップショット':
        st.caption('アプリが保存したworks/<作品ID>/sync/latest.jsonを、固定コミットから検証して取得します。まだ正式採用しません。')
        if st.button('最新スナップショットを候補取得',type='primary'):
            perform(lambda:service.fetch_bundle(branch,expected_revision=data['revision']),'スナップショットを候補保存しました。差分確認へ進んでください。')
        return
    if st.button('GitHubの作品ファイル一覧を取得'):
        def action():st.session_state[f'{prefix}_browse'] = dict(branch=branch,result=service.browse(branch))
        perform(action,'作品ファイルの一覧を取得しました。')
    browsed=st.session_state.get(f'{prefix}_browse')
    if not browsed or browsed['branch']!=branch:st.info('一覧取得後、対象ファイルを選択できます。');return
    names=list(browsed['result']['files']);jsons=[p for p in names if p.endswith('.json') and '/sync/' not in p]
    st.caption(f'一覧のコミット：{browsed["result"]["commit"]}。取得時にもブランチを確認し、その時点の固定コミットで保存します。')
    if not jsons:st.info('選択できるJSONがありません。スナップショット方式を利用してください。');return
    if mode=='AI提案・画像レビューの回答':
        path=st.selectbox('取得する回答JSON',jsons,key=f'{prefix}_response_path')
        kind=st.selectbox('既存の取り込み先',('assistant','review'),format_func=lambda k:'制作アシスタントの未採用提案' if k=='assistant' else '完成画像レビューの未確認提案')
        st.caption('ローカルで作成した依頼ID・作品ID・脚本版・対象・設定と照合します。AI提案の正式採用や画像の自動修正は行いません。')
        if st.button('回答JSONを検証して候補取得'):
            perform(lambda:fetch_response(service,branch,path,kind,expected_revision=data['revision']),'回答JSONを候補保存しました。')
        return
    story=st.selectbox('ストーリーJSON（必須）',jsons,index=next((i for i,p in enumerate(jsons) if '/story' in p),0),key=f'{prefix}_story')
    art=st.selectbox('人物・作画指示の既存提案JSON（任意）',[None]+jsons,format_func=lambda p:p or '指定しない',key=f'{prefix}_art')
    notes=st.multiselect('参考資料JSON（資料として保持・評価点に自動変換しない）',jsons,key=f'{prefix}_notes')
    pngs=[p for p in names if p.endswith('.png')]
    selected=st.multiselect('取り込むカラー画像・コマ画像（任意）',pngs,format_func=lambda path:unicodedata.normalize('NFC',path),key=f'{prefix}_pngs')
    images=[]
    for index,path in enumerate(selected):
        st.caption(path);cols=st.columns(2)
        page=cols[0].number_input(f'画像{index+1}のページ',min_value=1,max_value=100,value=1,key=f'{prefix}_{sha256(path.encode())}_page')
        panel=cols[1].number_input(f'画像{index+1}のコマ',min_value=1,max_value=6,value=min(index+1,6),key=f'{prefix}_{sha256(path.encode())}_panel')
        images.append(dict(path=path,page=page,panel=panel))
    st.caption('画像を選んだ順だけでは構図・人物を推測しません。ページ・コマとの対応を確認してください。元画像と既存資料はGitHub上でも変更しません。')
    if st.button('選択したファイルを候補取得',type='primary'):
        perform(lambda:service.fetch_legacy(branch,story,artwork_path=art,images=images,attachments=notes,expected_revision=data['revision']),'選択作品の候補を保存しました。差分確認へ進んでください。')


def candidate_view(service,data,prefix,perform,navigate):
    st.subheader('2. 差分を確認して、採用する範囲を選ぶ')
    if not data['candidates']:st.info('先にGitHubから候補を取得してください。');return
    shown=page_items(list(reversed(data['candidates'])),f'{prefix}_candidate_batch','同期候補',size=10)
    by_id={c['id']:c for c in shown}
    selected=st.selectbox('確認する同期候補',list(by_id),format_func=lambda i:f'{by_id[i]["saved_at"]} / {by_id[i]["status"]} / {i[:8]}',key=f'{prefix}_candidate')
    candidate=by_id[selected]
    st.caption(f'取得元：{candidate["repository"]} / {candidate["source_branch"]} / {candidate["source_commit"]}')
    st.dataframe([{k:v for k,v in row.items() if k!='changes'} for row in candidate['differences']],hide_index=True,use_container_width=True)
    if st.checkbox('現在のローカルとの詳細差分を表示',key=f'{prefix}_{selected}_diff'):
        with st.expander('JSON構造に基づく実際の差分',expanded=True):st.json(service.diff(selected),expanded=False)
    files=[e['path'] for e in candidate['manifest']['files']]
    if any(p.startswith('responses/') for p in files):
        if st.checkbox('既存の未採用提案・未確認レビューへ登録する内容を確認した',key=f'{prefix}_{selected}_response_ack') and st.button('この回答を既存の提案管理へ登録'):
            perform(lambda:accept_response(service,selected,expected_revision=data['revision']),'既存の提案・品質レビューへ未確認として登録しました。正式採用は各画面で行います。')
    else:
        scopes=st.multiselect('正式採用する内容（初期値は未選択）',list(SCOPES),format_func=SCOPES.get,key=f'{prefix}_{selected}_scopes')
        selections=st.checkbox('同期元の採用画像選択も明示的に反映する',value=False,key=f'{prefix}_{selected}_selection')
        st.caption('画像は通常、候補登録だけです。人物ID・基準画像は固定し、矛盾するID・ハッシュがあれば停止します。ページ編集を選ぶと既存配置の変更前を履歴・バックアップに保存します。')
        ack=st.checkbox('差分と採用範囲を確認した',key=f'{prefix}_{selected}_ack')
        if st.button('選んだ内容をローカルへ明示採用',type='primary',disabled=not scopes or not ack):
            def action():
                service.adopt(selected,scopes,expected_revision=data['revision'],adopt_selections=selections)
                project=ProjectStore(service.directory).load(service.project_id);st.session_state['active_project']=project
                for name in ('title','genre','synopsis','page_count'):st.session_state[name]=getattr(project,name)
            perform(action,'確認した内容だけを採用しました。既存履歴・画像を保持しています。')
    with st.expander('取得した資料・完成ファイル・技術情報'):
        st.json(candidate['manifest'],expanded=False)
        if st.checkbox('この候補のファイルをダウンロードする',key=f'{prefix}_{selected}_files'):
            content=service._files(candidate)
            for path in files:
                if path.endswith('.json') or path.startswith('exports/'):st.download_button(path,content[path],file_name=path.split('/')[-1],key=f'{prefix}_{selected}_{path}')
    registered=(service.directory/f'{service.project_id}.json').is_file()
    if not registered:st.caption('品質チェックへ進むには、先にストーリーを選んで作品を明示採用してください。')
    if st.button('採用した作品の品質チェックを開く',disabled=not registered):
        project=ProjectStore(service.directory).load(service.project_id);st.session_state['active_project']=project
        navigate('完成・書き出し','品質チェック・修正管理')


def publish_view(service,data,source_branch,prefix,perform):
    st.subheader('3. この作品だけをGitHubへ保存')
    st.warning('選択作品のJSON・参照画像・候補画像が公開先リポジトリに保存されます。公開リポジトリでは閲覧可能になるため、対象一覧を確認してください。data/projects全体は送信しません。')
    branch=st.text_input('保存先の作品同期ブランチ',value=f'studio-sync/{service.project_id}',key=f'{prefix}_publish_branch')
    index=output_index(service.directory,service.project_id)
    options={i['id']:i for i in index['items']}
    selected=st.multiselect('保存済み完成PNG・PDFを含める（任意）',list(options),format_func=lambda i:f'{options[i]["filename"]} / {options[i]["saved_at"]}',key=f'{prefix}_outputs')
    st.caption('完成ファイルはPNG・PDF書き出し画面の「GitHub同期用にローカル保存」で登録します。1件10MiB、1回合計100MiB、300ファイルまで。main/masterへは書き込みません。')
    if st.button('送信対象と容量を確認する計画を作成'):
        def action():
            outputs={}
            source_fingerprint=current_output_fingerprint(service.directory,service.project_id) if selected else None
            for identifier in selected:
                item=options[identifier]
                if item['source_fingerprint']!=source_fingerprint:raise ValueError('古い制作データの完成ファイルが選択されています。最新の作品で出力し直してください。')
                outputs[f'{identifier}_{item["filename"]}']=read_output(service.directory,service.project_id,item)
            service.prepare_export(branch,expected_revision=data['revision'],outputs=outputs,source_branch=source_branch,output_fingerprint=source_fingerprint)
        perform(action,'作品の送信計画を保存しました。対象と容量を確認してから送信してください。')
    pending=[r for r in reversed(data['exports']) if r['status']=='確認待ち' and r['branch']==branch]
    if pending:
        plan=pending[0];entries=plan['manifest']['files']
        st.caption(f'対象作品：{service.project_id} ｜ {len(entries)}ファイル ｜ {sum(e["size"] for e in entries)/1024/1024:.2f}MiB')
        st.dataframe(entries,hide_index=True,use_container_width=True)
        ack=st.checkbox('この作品の表示されたファイルをGitHubへ保存する',key=f'{prefix}_{plan["id"]}_publish_ack')
        if st.button('確認した作品スナップショットをGitHubへ保存',disabled=not ack,type='primary'):
            perform(lambda:service.push(plan['id'],expected_revision=data['revision']),'GitHubの作品ブランチへ保存しました。mainへのマージは行っていません。')
    for row in page_items([r for r in reversed(data['exports']) if r['status']=='保存済み'],f'{prefix}_published_batch','公開済み記録',size=5):
        st.code(f'{row["branch"]}\n{row["commit"]}',language='text')


def pdca_view(service,data,prefix,perform,navigate):
    st.subheader('品質・PDCA（人間の操作で進める）')
    st.caption('ChatGPTの自動呼び出しはありません。新しいGitHub成果物を候補取得し、採用・既存の品質チェック・再評価を繰り返します。')
    policy=data['policy'];cols=st.columns(2)
    maximum=cols[0].number_input('改善回数の上限',min_value=1,max_value=100,value=policy['max_iterations'],key=f'{prefix}_max')
    minimum=cols[1].number_input('参考評価点の基準（100点満点）',min_value=0,max_value=100,value=policy['minimum_score'],key=f'{prefix}_score_min')
    if st.button('PDCA基準を保存'):
        perform(lambda:set_policy(service.store,maximum,minimum,expected_revision=data['revision']),'PDCA基準を履歴付きで保存しました。')
    if data['candidates']:
        by_id={c['id']:c for c in data['candidates']}
        selected=st.selectbox('今回の評価対象候補',list(by_id),format_func=lambda i:f'{i[:8]} / {by_id[i]["status"]}',key=f'{prefix}_pdca_candidate')
        has_score=st.checkbox('主観的な参考評価点を記録する',key=f'{prefix}_has_score')
        score=st.number_input('今回の参考評価点',min_value=0,max_value=100,value=80,disabled=not has_score,key=f'{prefix}_iteration_score') if has_score else None
        checked=st.checkbox('現在の採用版と品質レビューを人間が最終確認した',key=f'{prefix}_iteration_confirm')
        reason=st.text_input('利用者による停止理由（停止する場合だけ入力）',max_chars=2000,key=f'{prefix}_stop_reason')
        if st.button('今回の改善・再評価履歴を記録'):
            perform(lambda:record_iteration(service,selected,expected_revision=data['revision'],score=score,confirmed=checked,stop_reason=reason),'改善・再評価の確認状態と停止理由を記録しました。')
    if data['iterations']:st.dataframe([{k:v for k,v in r.items() if k not in ('quality','policy')} for r in data['iterations']],hide_index=True,use_container_width=True)
    with st.expander('同期・採用・基準変更の履歴'):
        for row in page_items(list(reversed(data['events'])),f'{prefix}_history','同期履歴',size=10):st.json(row,expanded=False)
    st.caption('既存のreview.jsonの検査・目視・修正タスクを使用します。未確認、古い版、未解決タスクが残る場合は、点数だけで品質達成にしません。')
