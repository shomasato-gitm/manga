"""実際のJSON差分と版に結び付いた評価を比較し、任意の候補を明示採用する。"""
import json

from manga.ui_runtime import ui as st
from manga.editorial_models import CRITERIA,SUBJECTIVE_NOTICE
from manga.story_comparison import compare_stories
from manga.story_ai_results import evaluation
from manga.story_ai_ui import action


def comparison(service,data,prefix,navigate):
    st.caption(SUBJECTIVE_NOTICE)
    jobs={j['id']:j for j in reversed(data['jobs'][-30:]) if any(c['job_id']==j['id'] for c in data['candidates'])}
    if not jobs:st.info('脚本候補はまだありません。脚本制作から開始してください。');return
    job_id=st.selectbox('比較する制作履歴',list(jobs),format_func=lambda i:jobs[i]['created_at']+' / '+i[:8],key=prefix+'_compare_job')
    candidates={c['id']:c for c in data['candidates'] if c['job_id']==job_id};ids=list(candidates)
    def label(i):
        score=evaluation(data,i)
        return f"改善{candidates[i]['cycle']}回 / {score['payload']['total_score'] if score else '未評価'}点 / {i[:8]}"
    rows=[]
    adopted={a['candidate_id'] for a in data['adoptions']}
    for candidate in candidates.values():
        e=evaluation(data,candidate['id'])
        rows.append(dict(版=candidate['id'][:8],改善回数=candidate['cycle'],点数=e['payload']['total_score'] if e else None,状態='正式採用済み（履歴）' if candidate['id'] in adopted else candidate['decision']))
    st.dataframe(rows,hide_index=True,use_container_width=True)
    chart={r['版']:r['点数'] for r in rows if r['点数'] is not None}
    if chart:st.vega_lite_chart(spec=dict(data=dict(values=[dict(版=k,評価点=v) for k,v in chart.items()]),mark=dict(type='line',point=True),
        encoding=dict(x=dict(field='版',type='ordinal',sort=list(chart)),y=dict(field='評価点',type='quantitative',scale=dict(domain=[0,100])))),use_container_width=True)
    before=st.selectbox('比較元の候補',ids,format_func=label,key=prefix+'_'+job_id+'_before')
    best=jobs[job_id]['best_candidate_id'];index=ids.index(best) if best in ids else len(ids)-1
    after=st.selectbox('比較先・採用する候補',ids,index=index,format_func=label,key=prefix+'_'+job_id+'_after')
    old,new=candidates[before],candidates[after];a,b=evaluation(data,before),evaluation(data,after)
    if a and b:st.dataframe([dict(項目=label,変更前=a['payload']['criteria'][k]['score'],変更後=b['payload']['criteria'][k]['score'],差=b['payload']['criteria'][k]['score']-a['payload']['criteria'][k]['score']) for k,label in CRITERIA.items()],hide_index=True)
    st.write('変更理由（生成元の親版から）：'+new['change_reason'])
    with st.expander('変更されたページ・コマ・描写・セリフの実差分'):
        st.json(compare_stories(old['story'],new['story']),expanded=False)
    if b:
        with st.expander('問題点・根拠・対象コマ・改善案'):st.json(b['payload'],expanded=False)
    page=st.selectbox('確認する候補ページ',list(range(1,len(new['story']['pages'])+1)),key=prefix+'_'+after+'_page')
    st.caption('ページの引き：'+new['page_hooks'][page-1]['hook'])
    for panel in new['story']['pages'][page-1]['panels']:
        st.write(f"{panel['panel']}コマ：{panel['description']}")
        if panel['dialogue']:st.write(panel['dialogue'])
        if panel['narration']:st.caption(panel['narration'])
    st.download_button('候補脚本JSONを取得（既存schema_version:1）',json.dumps(new['story'],ensure_ascii=False,indent=2),file_name='story_candidate_'+after+'.json',mime='application/json')
    for warning in new['warnings']:st.warning(warning)
    if not b or new['decision']!='自動推薦候補（未採用）':st.warning('未評価・低下を含む候補です。自動推薦と正式採用は別です。選択内容を確認してください。')
    st.warning('採用は正式脚本の新しい版を追加します。既存の作画指示・画像・音声・動画は保持しますが、元脚本との整合性を再確認してください。脚本確定は従来画面で別途行います。')
    accepted=st.checkbox('この候補の脚本・評価・差分と、後工程への影響を確認した',key=prefix+'_'+after+'_adopt')
    history=service.stories.load_history();current=history[-1]['id'] if history else None
    if st.button('この候補を正式脚本の新しい版として採用',disabled=not accepted,type='primary'):
        action(lambda:service.adopt(after,expected_revision=data['revision'],expected_story_id=current,confirmed=accepted),'正式脚本に新しい版を追加しました。後工程の版を再確認してください。')
    if navigate and st.button('採用後のキャラクター設定へ進む'):navigate('キャラクター・作風','AI一括生成・採用')
