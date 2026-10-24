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
        for column,idea in zip(st.columns(len(batch['ideas'])),batch['ideas']):
            with column:
                st.markdown('**'+idea['title']+'**');st.write(idea['logline']);st.caption('葛藤：'+idea['conflict']);st.caption('結末：'+idea['ending'])
                with st.expander('あらすじ・人物・独自性・懸念'):
                    for key in ('synopsis','protagonist_goal','characters','turning_point','originality','risks'):st.write(idea[key])
        ideas={i['id']:i for i in batch['ideas']}
        selected=st.radio('詳細脚本に使う企画を1案選ぶ',list(ideas),format_func=lambda i:ideas[i]['title'],key=prefix+'_'+identifier+'_idea')
        if st.button('この企画を明示選択する',type='primary'):
            action(lambda:service.select_concept(selected,expected_revision=data['revision']),'企画を選択しました。次は「脚本制作・自動改善」です。不採用案も保持しています。')
    else:st.info('企画案はまだありません。生成後に比較して1案を選択します。')
    jobs(service,data,prefix)
