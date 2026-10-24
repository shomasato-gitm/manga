"""用途別の独立した順序・発話選択・focus・字幕を編集する。"""
import copy
from uuid import uuid4

from manga.ui_runtime import ui as st
from manga.voice_models import digest
from manga.video_models import FORMATS,MOTIONS,compile_plan


def unit(row):return 'u:'+row['utterance_id'] if row['utterance_id'] else 's:'+row['scene_id']


def editor(service,data,timeline,plans,prefix,selection_key=None):
    kind=st.selectbox('編集する動画形式',list(FORMATS),key=selection_key or prefix+'_kind');plan=plans[kind];key=prefix+'_'+kind
    st.caption('縦型は本編とは別のコマ・発話選択と順序です。発話途中の切断はできません。顔位置の自動検出は行いません。')
    options={};labels={}
    for scene in timeline['scenes']:
        name=f"{scene['page']}ページ{scene['panel']}コマ"
        options['s:'+scene['id']]=(scene['id'],None);labels['s:'+scene['id']]=name+'全体'
        for u in scene['utterances']:
            options['u:'+u['id']]=(scene['id'],u['id']);labels['u:'+u['id']]=name+' 発話：'+u['text'][:35]
    if kind!='youtube_main':
        picked=st.multiselect('使用するコマ・発話（選択順）',list(options),default=[unit(r) for r in plan['rows']],format_func=labels.get,key=key+'_units')
        if st.button('選択順を編集計画に反映',key=key+'_apply'):
            old={unit(r):r for r in plan['rows']};template=plan['rows'][0]
            plan['rows']=[copy.deepcopy(old.get(i) or dict(template,id=str(uuid4()),scene_id=options[i][0],utterance_id=options[i][1],gap_after_ms=200)) for i in picked]
            if plan['rows']:plan['rows'][-1]['gap_after_ms']=0
            st.rerun()
        plan['hook']=st.text_input('冒頭のフック文（任意・字幕とは別）',value=plan['hook'],max_chars=120,key=key+'_hook')
    if not plan['rows']:st.warning('少なくとも1コマまたは1発話を選択してください。');return
    row_id=st.selectbox('編集するシーン',[r['id'] for r in plan['rows']],format_func=lambda i:labels[unit(next(r for r in plan['rows'] if r['id']==i))],key=key+'_row')
    index=next(i for i,r in enumerate(plan['rows']) if r['id']==row_id);row=plan['rows'][index];rk=key+'_'+row_id
    if kind!='youtube_main':
        for delta,label in ((-1,'このシーンを前へ'),(1,'このシーンを後へ')):
            if st.button(label,disabled=not 0<=index+delta<len(plan['rows']),key=rk+str(delta)):
                plan['rows'][index],plan['rows'][index+delta]=plan['rows'][index+delta],plan['rows'][index];st.rerun()
        row['gap_after_ms']=st.number_input('このシーン後の余韻（ms）',min_value=0,max_value=5000,value=row['gap_after_ms'],key=rk+'_gap')
    row['motion']=st.selectbox('モーション',MOTIONS,index=MOTIONS.index(row['motion']),key=rk+'_motion')
    row['transition']=st.selectbox('場面転換',('cut','fade'),index=('cut','fade').index(row['transition']),key=rk+'_transition')
    row['framing']=st.selectbox('画像の収め方',('fit','focus'),index=('fit','focus').index(row['framing']),format_func=lambda v:'全体を安全に表示' if v=='fit' else '指定した重要領域を残してトリミング',key=rk+'_framing')
    if row['framing']=='focus':
        st.caption('重要な人物・情報を囲む領域を元画像の0〜1で指定します。全域を残せない切り取りは全体表示に戻します。')
        row['focus']=[st.number_input(label,min_value=0.0,max_value=1.0,value=float(row['focus'][i]),step=.05,key=rk+f'_focus_{i}') for i,label in enumerate(('重要領域の左','重要領域の上','重要領域の幅','重要領域の高さ'))]
    plan['subtitle_position']=st.selectbox('字幕の位置',('bottom','top'),index=('bottom','top').index(plan['subtitle_position']),format_func=lambda v:'下部' if v=='bottom' else '上部',key=key+'_subtitle')
    with st.expander('FPS・品質・日本語フォント'):
        plan['fps']=st.selectbox('動画FPS',(24,30,12),index=(24,30,12).index(plan['fps']),key=key+'_fps')
        plan['quality']=st.selectbox('エンコード設定',('balanced','fast'),index=('balanced','fast').index(plan['quality']),format_func=lambda v:'標準（veryfast）' if v=='balanced' else '軽量（ultrafast）',key=key+'_quality')
        plan['font_path']=st.text_input('日本語フォントのパス（空欄は自動検出）',value=plan['font_path'],key=key+'_font')
    from manga.video_ui_media import media
    media(service,data,plan,key)
    plan['rights_reviewed']=st.checkbox('使用画像・音声・任意素材の利用権を確認した',value=plan['rights_reviewed'],key=key+'_rights')
    body=lambda p:{k:v for k,v in p.items() if k not in ('editor_reviewed','rights_reviewed')}
    same=body(plan)==body(data['plans'].get(timeline['id'],{}).get(kind,{}))
    plan['editor_reviewed']=st.checkbox('この編集計画の意味・重要人物・字幕配置を確認した',value=bool(same and plan['editor_reviewed']),key=key+'_review_'+digest(body(plan)))
    try:
        schedule=compile_plan(plan,timeline);st.write(f"現在の計画：{len(plan['rows'])}シーン / {schedule['duration_ms']/1000:.2f}秒")
        maximum=max(0,schedule['rows'][index]['end_ms']-schedule['rows'][index]['start_ms']-1)
        time_ms=st.number_input('プレビュー時刻（シーン内ms）',min_value=0,max_value=maximum,value=0,key=rk+'_time_'+str(maximum))
        if st.button('このシーンの軽量プレビュー',key=rk+'_preview'):
            content,geometry=service.preview(timeline['id'],plan,index,time_ms=time_ms)
            st.image(content,caption='静止画の配置・字幕確認。実際のモーションと同期は生成MP4で確認します。')
            if geometry['focus_fallback']:st.warning('重要領域を残して切り取れないため、全体表示に戻しました。')
    except (ValueError,OSError) as error:st.error(str(error))
