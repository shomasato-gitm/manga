"""3〜5件だけをカードで比較し、選ばなかった企画も履歴として保持する。"""
from manga.ui_runtime import ui as st
from manga.story_ai_ui import action
from manga.story_ai_ui_jobs import request_controls,jobs


def concepts(service,data,provider,prefix):
    request_controls(service,data,'concepts',provider,prefix)
    batches={b['id']:b for b in reversed(data['concept_batches'][-30:])}
    if batches:
        identifier=st.selectbox('比較する企画セット',list(batches),format_func=lambda i:batches[i]['created_at']+' / '+i[:8],key=prefix+'_batch')
        batch=batches[identifier]
        st.caption('ここでは脚本制作に使う企画を選びます。正式な脚本の採用・確定は、脚本比較画面で別に行います。')
        for offset in range(0,len(batch['ideas']),2):
            for column,idea in zip(st.columns(2),batch['ideas'][offset:offset+2]):
                with column:
                    with st.container(border=True):
                        st.subheader(idea['title']); st.write(idea['synopsis'][:240]+('…' if len(idea['synopsis'])>240 else ''))
                        st.caption('見どころ：'+idea['logline'])
                        st.write('葛藤：'+idea['conflict'][:160]); st.write('結末：'+idea['ending'][:160])
                        st.caption('企画単体の点数：未評価。脚本候補の評価とは別です。')
                        if st.button('この企画を選択',key=prefix+'_choose_'+idea['id'],type='primary'):
                            def choose(idea=idea):
                                service.select_concept(idea['id'],expected_revision=data['revision'])
                                st.session_state[prefix+'_'+identifier+'_idea']=idea['id']
                            action(choose,'脚本制作に使う企画を選びました。正式脚本はまだ変更していません。')
                        with st.expander('この企画の詳細'):
                            for key in ('synopsis','conflict','ending','protagonist_goal','characters','turning_point','originality','risks'): st.write(idea[key])
        ideas={i['id']:i for i in batch['ideas']}
        selected=st.radio('詳細脚本に使う企画を1案選ぶ',list(ideas),format_func=lambda i:ideas[i]['title'],key=prefix+'_'+identifier+'_idea')
        with st.container(border=True):
            st.subheader('選択中の企画：'+ideas[selected]['title'])
            st.write(ideas[selected]['synopsis'])
            st.caption('候補の選択です。正式な脚本の採用・最終確定は別の操作です。')
        if st.button('この企画を明示選択する',type='primary'):
            action(lambda:service.select_concept(selected,expected_revision=data['revision']),'企画を選択しました。次は「脚本制作・自動改善」です。不採用案も保持しています。')
    else:st.info('企画案はまだありません。生成後に比較して1案を選択します。')
    jobs(service,data,prefix)
