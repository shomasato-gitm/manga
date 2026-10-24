"""利用者が保存した依頼だけを1件ずつ実行する。手動画像は元バイト列も保持。"""
from manga.image_ai_ui import action
from manga.ui_runtime import ui as st


def generation(service,data,prefix):
    saved=st.session_state.get(prefix+'_plan')
    if not saved: st.info('先に「生成計画」で対象・サイズ・参照を確認してください。'); return
    plan=saved['plan']
    st.caption(f"保存した計画：{plan['kind']} / {plan['character_id'] or str(plan['page'])+'ページ'+str(plan['panel'])+'コマ'} / {plan['options']['size']} / 参照{len(plan['reference_images'])}枚")
    provider=st.radio('画像候補の作り方',('mock','manual','paid-locked'),format_func=lambda p:{'mock':'無料モック（AI生成ではないテスト画像）','manual':'手動画像の取り込み','paid-locked':'有料API（未対応・送信不可）'}[p],key=prefix+'_provider')
    uploaded=None
    if provider=='manual': uploaded=st.file_uploader('手動画像ファイル（PNG・JPEG・WebP、20MBまで）',type=['png','jpg','jpeg','webp'],key=prefix+'_manual_image')
    if provider=='paid-locked': st.error('最大請求量が未確認のため、有料送信・新規予約は行いません。')
    else: st.info('API送信0回・0円。ここでは正規画像を変更しません。')
    new=st.checkbox('同じ条件でも新しい候補の依頼を作る（過去候補を保持）',key=prefix+'_new_job')
    if st.button('候補作成の依頼を保存（正規画像は変更しない）',type='primary',disabled=provider=='paid-locked' or provider=='manual' and uploaded is None):
        action(lambda:service.create_job(plan,provider=provider,expected_revision=data['revision'],new_request=new,content=uploaded.getvalue() if uploaded else None,filename=uploaded.name if uploaded else None), '依頼を保存しました。下で選択して1候補を作成してください。')
    jobs={j['id']:j for j in reversed(data['jobs']) if j['plan']['kind']==plan['kind'] and j['plan']['character_id']==plan['character_id'] and (j['plan']['page'],j['plan']['panel'])==(plan['page'],plan['panel'])}
    if not jobs: return
    identifier=st.selectbox('実行する画像依頼',list(jobs),format_func=lambda i:jobs[i]['state']+' / '+jobs[i]['provider']+' / '+i[:8],key=prefix+'_execute_job')
    job=jobs[identifier]
    st.write('状態：'+job['state']+' / request_id：'+identifier+' / API費用0円')
    if st.button('選択した依頼から1候補を作成',type='primary',disabled=job['state']!='queued'):
        action(lambda:service.run(identifier), '候補を保存しました。「候補比較・採用」で人間が確認してください。AI生成ではないモックと手動画像を区別しています。')
    if st.button('選択した画像依頼を停止',disabled=job['state'] not in ('queued','running','artifact_pending')):
        action(lambda:service.cancel(identifier), '停止を要求しました。保存済み候補・元画像は削除していません。')


def history(service,data,prefix):
    st.subheader('画像制作の履歴・保存復旧')
    if not data['jobs']: st.info('画像依頼は未作成です。'); return
    jobs={j['id']:j for j in reversed(data['jobs'])}
    identifier=st.selectbox('履歴・復旧の画像依頼',list(jobs),format_func=lambda i:jobs[i]['state']+' / '+jobs[i]['provider']+' / '+i[:8],key=prefix+'_history_job')
    job=jobs[identifier]
    st.caption('API送信0回、課金予約0円、費用0円。実APIの請求不明を自動解放する機能はありません。')
    if job['error']: st.warning(job['error'])
    with st.expander('依頼・参照ID・プロンプト・保存状態の詳細'): st.json(job,expanded=False)
    replacement=st.file_uploader('元入力が未保存の場合だけ同じ画像を再選択',type=['png','jpg','jpeg','webp'],key=prefix+'_'+identifier+'_resume_file') if job['provider']=='manual' else None
    confirmed=st.checkbox('同じ依頼IDの保存だけを無料で再開することを確認した',key=prefix+'_'+identifier+'_resume')
    if st.button('選択した画像依頼の保存を再開',disabled=not confirmed or job['state']=='cancelled'):
        action(lambda:service.recover(identifier,confirmed=confirmed,content=replacement.getvalue() if replacement else None),'同じ依頼IDで保存を再開しました。有料APIは再送していません。')
    related=[a for a in data['adoptions'] if a['candidate_id']==job['candidate_id']]
    with st.expander('この依頼の正規登録・採用履歴'): st.json(related,expanded=False)
