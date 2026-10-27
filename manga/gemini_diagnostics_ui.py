"""料金を保存する前から利用できる初回診断。チェックだけでは通信しない。"""
from manga.ui_runtime import ui as st
from manga.editorial_ui_common import input_text
from manga.story_ai_ui import action
from manga.gemini_diagnostics import offline_report,preview,run,recover,diagnostic_history,MESSAGES,probe_prompt
from manga.gemini_models import LEGACY_MODEL,get_model

LABELS={'processing':'処理中・中断時は照合待ち','success':'通信成功（診断時点）','unavailable':'利用不可・条件の再確認が必要','uncertain':'通信・請求結果の確認待ち'}


def initial_diagnostic(service,data,raw,prefix,selected_model=None):
    st.subheader('初回接続診断')
    st.caption('①通信なしで不足条件を確認 → ②料金・利用条件を確認 → ③固定文の実通信を個別承認 → ④結果を確認して料金設定へ戻る')
    st.info('APIキー設定やAI Studioの無料枠表示だけでは、このモデルの利用権限は確認できません。通信成功も無料枠の保証ではありません。')
    st.caption('この診断は固定文による疎通確認です。JSON構造化出力の設定は送信せず、通常の企画・脚本JSONの生成可否は確認しません。')
    def draft():
        try:return input_text(raw)
        except (ValueError,UnicodeError):return None
    if st.button('通信なしで設定を診断する',key=prefix+'_offline_diagnose'):
        st.session_state[prefix+'_offline_result']=offline_report(draft(),selected_model=selected_model)
    report=st.session_state.get(prefix+'_offline_result')
    if report:
        st.caption('通信なし診断の結果です。入力を変更した場合は、もう一度このボタンを押してください。')
        st.write('APIキー設定：'+('設定あり（形式のみ確認済み・認証未検証）' if report['key_format'] else '未設定または形式の確認が必要'))
        st.write('JSON：'+report['json_status']);st.write('公式モデル情報：'+report['model_information'])
        st.write('モデル利用権限：'+report['account_access']);st.write('無料枠：'+report['free_tier'])
        for item in report['missing']:st.warning(item)
        st.caption('通信回数0・予算予約0。この結果だけで権限フラグや料金JSONを保存することはありません。')
    st.markdown('**料金と利用条件を確認した後の実通信診断**')
    st.caption('上のJSONは未保存でも使用できます。account_access_reviewedは未確認のfalseのままで診断できます。他の料金・規約・送信条件の確認は必要です。')
    mode=st.selectbox('初回診断の利用モード',('bounded','verified-free'),format_func=lambda v:'課金の可能性あり・最大額を承認して診断' if v=='bounded' else '無料枠は本人確認済み・モデル権限は診断で確認',key=prefix+'_diagnostic_mode')
    st.warning('課金の可能性を含む診断はMANGA_ENABLE_PAID_STORY=1、無料枠本人確認済みの診断はMANGA_ENABLE_TEXT_API=1が必要です。無料枠から有料への自動切替はありません。')
    try:
        plan=preview(service,draft(),mode,selected_model=selected_model)
        from manga.budget.config import open_budget
        summary=None
        # 予算を見る操作も利用者が選ぶ。画面表示・通信なし診断はDBを作らない。
        if st.checkbox('共有予算の残額を確認して実通信の準備をする',key=prefix+'_diagnostic_budget'):
            summary=open_budget(service.directory).summary(service.project_id)
        st.write(f"モデル：{plan['model']} ／ 最大入力：{plan['limits']['max_input_tokens']:,} tokens ／ 最大出力（思考分含む）：256 tokens ／ 最大費用：{plan['maximum_jpy']}円")
        st.write('送信内容（この固定文だけ）：');st.code(probe_prompt(plan['model']),language=None)
        st.caption('作品名・脚本・人物・画像・音声は送信しません。APIキーは認証ヘッダーだけに使用します。検索・ツール・外部キャッシュ・再試行は使いません。入力上限はモデル全入力幅による保守的な見積です。')
        st.caption('思考設定：'+get_model(plan['model']).thinking_label)
        enabled=service.api_enabled('paid' if mode=='bounded' else 'verified-free')
        if summary:
            st.write(f"作品残予算：{summary['project_remaining_jpy']}円 ／ 全作品月間残予算：{summary['monthly_remaining_jpy']}円")
            enabled=enabled and not summary['blocked'] and float(plan['maximum_jpy'])<=min(summary['project_remaining_jpy'],summary['monthly_remaining_jpy'])
        else:enabled=False;st.caption('実行するには上のチェックで共有予算の残額を確認してください。')
        report_now=offline_report(draft(),selected_model=selected_model)
        enabled=enabled and report_now['key_format']
        consent=st.checkbox('料金・利用条件・送信内容・最大費用を確認し、この固定文の診断を1回だけ承認する',disabled=not enabled,key=prefix+'_'+plan['approval_key']+'_diagnostic_consent')
        if st.button('承認した初回接続診断を1回だけ実行',disabled=not enabled or not consent,key=prefix+'_live_diagnose'):
            def send():
                result=run(service,draft(),mode=mode,approval=plan['approval_key'],expected_revision=data['revision'])
                st.session_state[prefix+'_diagnostic_recover']=result['diagnosis_id']
            action(send,'診断結果を保存しました。下の結果・請求状態を確認してください。')
    except (ValueError,TypeError,ArithmeticError):
        st.caption('まだ実通信できません。まず「通信なしで設定を診断する」で不足項目を確認してください。利用権限を確認済みに偽って変更する必要はありません。')
    history=diagnostic_history(service)
    if history:
        st.markdown('**初回診断の履歴・結果**')
        st.caption('過去のキーでの結果です。現在のキーや無料枠の利用権限を保証しません。通常の脚本生成用設定や無料枠確認を自動更新しません。')
        labels={h['diagnosis_id']:f"診断{i+1} ／ {get_model(h.get('model',LEGACY_MODEL)).id} ／ {LABELS[h['state']]}" for i,h in enumerate(history)}
        identifier=st.selectbox('請求・保存を照合する診断',list(reversed(labels)),format_func=labels.get,key=prefix+'_diagnostic_recover')
        latest=next(h for h in history if h['diagnosis_id']==identifier);st.write(LABELS[latest['state']])
        st.write('この履歴のモデル：'+get_model(latest.get('model',LEGACY_MODEL)).id)
        if latest['reason']:st.warning(MESSAGES[latest['reason']])
        _stages(service,identifier)
        if latest['state']=='success':
            st.success('この診断ではモデルから有効な応答がありました。無料枠の適用・残枠・今後の権限は別に本人確認してください。')
            st.info('モデル利用条件を確認できた場合のみ、上のJSONのaccount_access_reviewedを本人の判断でtrueにし、従来の確認チェックから保存してください。診断はこの項目を変更しません。')
        else:st.warning('HTTPエラーや通信失敗でも予算予約を自動解放しません。予算画面で実際の請求を照合してください。')
        if st.button('診断の保存・請求を照合する（再送なし）',key=prefix+'_diagnostic_recover_button'):
            action(lambda:recover(service,identifier),'保存・請求の照合結果を反映しました。通信は再送していません。')


def _stages(service,identifier):
    from manga.gemini_diagnostic_trace import history,STAGES
    st.write('照合用リクエストID：'+identifier)
    records=history(service,identifier)
    if not records:
        st.info('段階別ログがない旧記録です。既存の分類だけでは通信失敗の正確な発生箇所を特定できません。未送信・無課金とは判断しません。')
        return
    st.markdown('**診断の段階別記録（再送なし）**')
    states={'started':'開始（完了は未記録）','completed':'完了記録あり','failed':'失敗記録あり'}
    st.dataframe([{'段階':STAGES[r['stage']],'状態':states[r['outcome']],'日時':r['created_at'],
        'HTTPステータス':str(r['http_status']) if r['http_status'] is not None else '記録なし',
        '分類':MESSAGES[r['reason']] if r['reason'] else ''} for r in records],hide_index=True)
    recorded={r['stage'] for r in records}
    st.caption('未記録の段階：'+('、'.join(label for stage,label in STAGES.items() if stage not in recorded) or 'なし'))
    st.caption('送信処理の開始はGoogleへの到達証明ではありません。未記録は未実行の証明ではありません。請求額は別途照合してください。本文・APIキー・ヘッダー・生の例外は記録しません。')
