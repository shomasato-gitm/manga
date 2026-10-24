"""上限見積と同意、バックグラウンドPDCA、停止・保存復旧。"""
from manga.ui_runtime import ui as st
from manga.budget.config import open_budget
from manga.story_ai_ui import action

REASONS={'concepts_ready':'企画案の確認待ち','goal_reached':'目標点に到達（人間の確認が必要）','improvement_limit':'改善回数の上限に到達','quality_regression':'品質低下・自動推薦を停止','no_change':'脚本に変更なし','budget_limit':'予算残額不足','provider_uncertain':'請求不明・再送禁止','artifact_pending':'応答の保存再開が必要','invalid_output':'回答形式の確認が必要','stale_sources':'元データ変更','user_cancelled':'利用者が停止','interrupted':'中断・再開確認','not_sent':'未送信・保存状態を確認','paid_disabled':'有料機能は無効','storage_failure':'保存先・容量を確認','ledger_mismatch':'予算台帳の保存先を確認'}


def request_controls(service,data,kind,provider,prefix):
    if data['settings'] is None:st.info('先に制作条件を保存してください。');return
    cap=st.number_input('この依頼のAPI上限（円・既存500円枠の内側）',min_value=1,max_value=500,value=500,key=prefix+'_'+kind+'_cap')
    new=st.checkbox('同条件の依頼を新規に作り直す（有料では再課金される）',key=prefix+'_'+kind+'_new')
    try:estimate=service.estimate(kind,provider=provider,spend_cap=str(cap),new_request=new)
    except ValueError as error:st.info(str(error));return
    st.write(f"1呼出し最大：{estimate['per_call_max_jpy']}円 ／ 最大{estimate['max_calls']}回 ／ 全工程の理論最大：{estimate['full_plan_max_jpy']}円")
    st.caption('実行上限は上記の依頼枠と、共通の作品生涯500円・全作品月間5,000円の小さい方です。5回は上限で、完走の保証ではありません。')
    approval=None;enabled=True
    if provider!='mock':
        if not estimate['full_plan_fits']:st.warning('全工程の理論最大額は依頼枠を超えます。各送信前に最大額を確保できなければ途中停止します。平均token数だけで安全と判断しません。')
        enabled=service.paid_enabled()
        if not enabled:st.warning('有料機能は無効です。明示承認前にAPIを呼びません。')
        summary=open_budget(service.directory).summary(service.project_id)
        st.write(f"共有残額：作品{summary['project_remaining_jpy']}円／月間{summary['monthly_remaining_jpy']}円")
        accepted=st.checkbox('今回の単発最大額・全工程最大額・実行上限・途中停止条件を確認し、有料API実行を明示承認する',disabled=not enabled,key=prefix+'_'+estimate['approval_key']+'_consent')
        enabled=enabled and accepted
        if enabled:approval=estimate['approval_key']
    if st.button('企画案を自動生成' if kind=='concepts' else '脚本生成・評価・自動改善を開始',type='primary',disabled=not enabled):
        def start():
            job=service.create_job(kind,expected_revision=data['revision'],provider=provider,spend_cap=str(cap),new_request=new,approval=approval)
            if job['state']=='queued':service.start(job['id'])
        action(start,'依頼を受け付けました。状態を更新し、完了後に内容を確認してください。同条件の依頼は履歴を再利用します。')


def workflow(service,data,provider,prefix):
    st.subheader('選択企画から脚本を制作')
    if data['selections']:
        idea,_=service.concept(data,data['selections'][-1]['concept_id']);st.write('選択中の企画：'+idea['title'])
        request_controls(service,data,'story',provider,prefix)
    else:st.info('「企画案の生成・比較」で1案を明示的に選んでください。')
    jobs(service,data,prefix)


def jobs(service,data,prefix):
    st.subheader('進行状況・停止理由')
    if st.button('AI制作の状態を更新'):st.rerun()
    for job in reversed(data['jobs'][-5:]):
        st.write(f"{job['kind']} / {job['state']} / {job['phase']} / 改善{job['cycle']}/{job['settings']['max_cycles']}回 / {job['id'][:8]}")
        count=sum(r['job_id']==job['id'] and r['state']=='applied' for r in data['requests'])
        st.caption(f'保存済み回答：{count}件。正式脚本への採用はまだ別操作です。')
        if job['stop_reason']:st.info(REASONS.get(job['stop_reason'],job['stop_reason']))
        if job['error']:st.warning(job['error'])
        if job['state'] in ('queued','running') and st.button('このAI制作を停止',key=prefix+job['id']+'_stop'):
            action(lambda:service.cancel(job['id']),'停止を要求しました。送信済み処理は応答と費用を保存してから停止します。')
    if not data['jobs']:return
    items={j['id']:j for j in reversed(data['jobs'][-30:])}
    identifier=st.selectbox('復旧・再開する依頼',list(items),format_func=lambda i:items[i]['state']+' / '+i[:8],key=prefix+'_recover')
    chosen=items[identifier]
    st.caption(f"選択依頼の元の上限：{chosen['spend_cap_jpy']}円 / 改善最大{chosen['settings']['max_cycles']}回 / {chosen['provider']}")
    with st.expander('選択依頼の制作条件・モデル・承認記録'):
        st.json(dict(settings=chosen['settings'],model=chosen['card']['model'] if chosen['card'] else 'mock',approval=chosen.get('approval')),expanded=False)
    if st.button('保存・請求状態の復旧を確認（APIを再送しない）'):action(lambda:service.recover(identifier),'保存状態を確認しました。必要なら残りの処理を明示的に再開してください。')
    accepted=st.checkbox('同じ依頼の残りだけを元の上限内で続行する',key=prefix+'_'+identifier+'_resume_ok')
    if st.button('停止後の続きを実行',disabled=not accepted):
        def resume():service.resume(identifier,confirmed=accepted);service.start(identifier)
        action(resume,'同じ依頼IDで残りの処理を再開しました。')
    requests={r['id']:r for r in data['requests'] if r['job_id']==identifier}
    if requests:
        with st.expander('保存済み応答の確認・取得（詳細）'):
            request_id=st.selectbox('確認するリクエスト',list(requests),format_func=lambda i:requests[i]['kind']+' / '+requests[i]['state']+' / '+i[:8],key=prefix+'_'+identifier+'_response')
            if st.checkbox('この応答本文を表示',key=prefix+'_'+request_id+'_show'):
                try:
                    import json
                    value=service.store.read(f'story_ai/responses/{request_id}.json')
                    st.code(json.loads(value)['response']['text'],language='json')
                    st.download_button('保存応答JSONを取得',value,file_name=request_id+'.json',mime='application/json')
                except (ValueError,OSError) as error:st.error(str(error))
