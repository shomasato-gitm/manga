"""制作条件と、公式確認日付きのモデル・料金設定。キー入力は設けない。"""
import json
from pathlib import Path

from manga.ui_runtime import ui as st
from manga.editorial_ui_common import json_input,input_text
from manga.story import load_json_payload
from manga.story_ai_pricing import template
from manga.story_ai_ui import action


def configuration(service,data,project,prefix):
    values=data['settings'] or dict(genre=project.genre,theme=project.synopsis,mood='温かく余韻のある雰囲気',page_count=project.page_count,concept_count=3,max_cycles=3,goal_score=85,stop_on_regression=True,reference_ids=[])
    st.subheader('制作条件（保存してから生成）')
    config=dict(genre=st.text_input('生成するジャンル',value=values['genre'],key=prefix+'_genre'),
        theme=st.text_area('物語のテーマ・伝えたいこと',value=values['theme'],key=prefix+'_theme'),
        mood=st.text_input('希望する雰囲気',value=values['mood'],key=prefix+'_mood'),
        page_count=st.number_input('AI脚本のページ数',1,100,value=values['page_count'],key=prefix+'_pages'),
        concept_count=st.selectbox('企画案の件数',(3,4,5),index=(3,4,5).index(values['concept_count']),key=prefix+'_count'),
        max_cycles=st.number_input('自動改善の上限回数',0,5,value=values['max_cycles'],key=prefix+'_cycles'),
        goal_score=st.number_input('AI評価の目標点（100点満点）',0,100,value=int(values['goal_score']),key=prefix+'_goal'),
        stop_on_regression=st.checkbox('総合点または評価項目が低下したら自動推薦せず停止する',value=values['stop_on_regression'],key=prefix+'_regression'))
    refs={r['id']:r for r in service.editorial.load()['references'] if not r['deleted_at']}
    config['reference_ids']=st.multiselect('参考にする作品（任意・3件まで）',list(refs),default=[r for r in values['reference_ids'] if r in refs],format_func=lambda i:refs[i]['title'],key=prefix+'_refs')
    st.caption('既存人物の設定を維持します。企画条件は独立保存し、プロジェクト設定を自動変更しません。正式採用前に作品設定のページ数を候補と合わせてください。')
    if st.button('AI制作条件を保存',type='primary'):action(lambda:service.configure(config,expected_revision=data['revision']),'制作条件を保存しました。次は企画案を生成します。')
    from manga.story_ai_text_ui import gemini_configuration
    gemini_configuration(service,data,config,prefix)
    with st.expander('有料APIのモデル・料金（公式確認後に設定）'):
        st.warning('実費テストは未承認です。設定を保存しても有料機能は有効になりません。APIキーはOPENAI_API_KEYから読み、JSONへ保存しません。')
        st.download_button('未記入のモデル・料金設定JSON',json.dumps(template(),ensure_ascii=False,indent=2),file_name='story_ai_pricing_template.json',mime='application/json')
        example=Path(__file__).resolve().parents[1]/'examples'/'story_ai'/'pricing_20261024.json'
        st.download_button('公式確認日付きの設定例（確認フラグfalse）',example.read_bytes(),file_name=example.name,mime='application/json')
        values_input=json_input('テキストAPI料金JSON',prefix+'_pricing')
        checked=st.checkbox('モデルの提供・構造化出力・商用条件・モデル最大入力幅と全加算料金を公式確認した',key=prefix+'_pricing_checked')
        if st.button('モデル・料金設定を保存',disabled=not checked):
            action(lambda:service.configure(config,load_json_payload(input_text(values_input)),expected_revision=data['revision']),'確認済みモデル・料金を保存しました。実行には別途の明示承認が必要です。')
