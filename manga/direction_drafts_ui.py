"""無料・オフラインの下書き比較。既存指示の上書き・暗黙保存を行わない。"""
from manga.direction_drafts import prepare_drafts, save_drafts
from manga.storage import StorageError
from manga.ui_runtime import ui as st


def batch_directions(store, prefix, run):
    with st.expander('複数コマの作画指示をまとめて準備（無料・オフライン）'):
        st.caption('正式脚本の明記情報と人物候補だけを下書きにします。空欄は推測で補いません。既存指示は上書きせず、選んで確認したコマだけを保存します。')
        if st.button('正式脚本から作画指示の下書きを一括準備'):
            try:
                st.session_state[prefix+'_batch'] = prepare_drafts(store)
            except (ValueError, OSError, StorageError) as error:
                st.warning(str(error))
        batch = st.session_state.get(prefix+'_batch')
        if not batch:
            return
        items = batch['items']
        people = {p['id']: p['name'] for p in store.load()['characters']}
        st.dataframe([dict(対象=f"{r['page']}ページ{r['panel']}コマ", 状態='既存指示を保持' if r['existing'] else '個別確認が必要' if r['issues'] else '保存候補',
                           人物候補='、'.join(people.get(i,'不明な人物') for i in r['fields']['character_ids']),
                           **{label:r['fields'][key] or '未指定' for key,label in (('location','場所'),('time_of_day','時間帯'),('expression','表情'),('pose','ポーズ'),('composition','構図'),('background','背景'))},
                           理由=' / '.join(r['issues'])) for r in items], hide_index=True, use_container_width=True)
        key = st.selectbox('下書きの詳細を確認するコマ',[r['key'] for r in items],key=prefix+'_batch_detail')
        row = next(r for r in items if r['key']==key)
        st.write('脚本：'+row['source_panel']['description'])
        st.write('根拠：'+' / '.join(row['evidence']))
        st.write('画像アスペクト比：'+row['hints'].get('aspect_ratio','脚本に明記なし。個別コマのレイアウト案内で確認してください。'))
        if row['issues']:
            st.warning(' / '.join(row['issues']))
        if st.button('この下書きのコマを個別に確認・修正'):
            st.session_state[prefix+'_jump'] = (row['page'],row['panel'])
            st.rerun()
        st.caption('修正したい候補は対象コマを開き、STEP 2で個別入力してください。一括保存時には基準画像・バイブルの承認は行いません。')
        choices = [r['key'] for r in items if not r['existing'] and not r['issues']]
        selected = st.multiselect('確認して保存するコマ',choices,key=prefix+'_batch_selected_'+str(batch['revision']))
        confirmed = st.checkbox('選択した各コマの人物候補と脚本の対応・空欄の任意項目を確認した',key=prefix+'_batch_confirm_'+str(batch['revision']))
        if st.button('選択したコマの作画指示を保存',disabled=not selected or not confirmed):
            def persist():
                result = save_drafts(store,batch,selected,confirmed=confirmed)
                st.session_state[prefix+'_batch_result'] = result
                st.session_state.pop(prefix+'_batch',None)
            run(persist,'選択対象の保存結果を確認してください。保存済み作画指示からプロンプトを準備できます。')
    result = st.session_state.get(prefix+'_batch_result')
    if result:
        if result['saved']:
            st.success('作画指示を保存したコマ：'+'、'.join(result['saved']))
        for key, reason in result['errors'].items():
            st.error(key+'は未保存です。'+reason+' 保存済みのコマは保持しています。候補を準備し直して未保存コマだけを選んでください。')
