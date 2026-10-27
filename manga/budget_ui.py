"""予算明細と、人間が確定明細を確認した後だけ行う手動照合。HTTP送信なし。"""
import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

from manga.budget.config import open_budget
from manga.ui_runtime import ui as st


def render_budget(project, projects_directory, navigate=None):
    if project is None:
        st.info("まず作品を選択してください。")
        return

    try:
        ledger = open_budget(projects_directory)
        info = ledger.summary(project.id)
    except (sqlite3.Error, OSError, ValueError):
        st.error('予算台帳を安全に読み込めません。API処理を停止してください。台帳を削除・再作成せず、保存先・権限・予算設定・バックアップを確認してください。')
        return

    st.subheader("共通API予算・請求照合")
    if '_budget_reconcile_notice' in st.session_state:
        st.success(st.session_state.pop('_budget_reconcile_notice'))
    if '_budget_reconcile_error' in st.session_state:
        st.error(st.session_state.pop('_budget_reconcile_error'))
    st.caption("残額は共通台帳に記録された予約・精算額です。接続可否・無料枠・提供元の請求上限は別途確認してください。")
    st.caption('台帳の保存先：'+str(ledger.path))
    st.write(f"作品: {project.title}")
    st.write(
        f"作品上限: {info['project_limit_jpy']:.2f}円 / "
        f"予約・利用済み: {_money(info['project_used_jpy'])}円 / "
        f"残額: {_money(info['project_remaining_jpy'])}円"
    )
    st.write(
        f"全作品月間 ({info['month']}): "
        f"{_money(info['monthly_used_jpy'])} / {info['monthly_limit_jpy']:.2f}円 / "
        f"月間残額: {_money(info['monthly_remaining_jpy'])}円"
    )
    pending = sum(info['requests_by_status'].get(k,0) for k in ('reserved','uncertain'))
    st.write(f"請求未確定: この作品 {pending}件 / 全作品 {info['monthly_pending_count']}件（過去月からの持ち越しを含む）")
    if info['blocked']:
        st.warning(f"新しいAPI実行を停止中です。全作品の請求額不明 {info['uncertain_count']}件、未確認の見積超過 {info['unreviewed_overrun_count']}件。明細と料金見積を手動確認してください。")
    st.caption(
        "予約金額は課金額とは限りません。タイムアウト時の予約は維持されます。"
        "アプリ内の上限では提供元の最終請求額を厳密に保証できません。"
    )
    try:
        _recovery(ledger,project.id)
    except (sqlite3.Error,OSError,ValueError):
        st.error('未確定明細を安全に読み込めません。台帳を変更・初期化せず、保存先とバックアップを確認してください。')
    if navigate and st.button('Gemini初回診断の保存・請求照合へ戻る'):
        navigate('企画・ストーリー','AI企画・ストーリー')


def _recovery(ledger,project_id):
    st.subheader('請求未確定のリクエスト明細')
    st.warning('AI Studioの「データなし」は未送信・無課金の証明ではありません。HTTPエラーや無料Tierでも予約を自動解除しません。')
    rows=ledger.pending_requests()
    rows.sort(key=lambda r:r['project_id']!=project_id)
    if not rows:
        st.info('請求未確定のリクエストはありません。')
    else:
        st.dataframe([{'リクエストID':r['request_id'],'作品UUID':r['project_id'],'提供元':r['provider'],
            'モデル':r['model'],'用途':r['kind'],'状態':'請求不明' if r['state']=='uncertain' else '予約中',
            '最大予約額（円）':_money(r['quote_jpy']),'作成日時':r['created_at'],'更新日時':r['updated_at']} for r in rows],hide_index=True)
        labels={r['request_id']:f"{r['request_id']} ／ {'この作品' if r['project_id']==project_id else '別作品'} ／ {_money(r['quote_jpy'])}円" for r in rows}
        identifier=st.selectbox('照合する未確定リクエスト',list(labels),format_func=labels.get,key='budget_recovery_request')
        if st.session_state.get('_budget_reconcile_target')!=identifier:
            st.session_state['_budget_reconcile_target']=identifier
            st.session_state['_budget_reconcile_plan']=None
            st.session_state['_budget_reconcile_nonce']=st.session_state.get('_budget_reconcile_nonce',0)+1
        row=next(r for r in rows if r['request_id']==identifier)
        st.write(f"対象作品UUID：{row['project_id']} ／ リクエストID：{identifier}")
        st.write(f"提供元：{row['provider']} ／ モデル：{row['model']} ／ 最大予約額：{_money(row['quote_jpy'])}円")
        st.caption('実行権の取得記録：'+('あり（実際の送信・到達の証明ではありません）' if row['execution_claimed'] else 'なし'))
        st.markdown('**提供元の明細を確認 → この1件と照合 → 診断画面で保存を再開（再送なし）**')
        st.caption('Geminiは対象Googleプロジェクト・キーの所属、実行日時、モデル、利用履歴と確定した請求額を照合してください。他の提供元はその確定明細を確認してください。反映待ち・集計のみで1件に対応付けられない場合は不明のまま保持します。明細やキーをここへ貼り付けないでください。')
        if row['state']!='uncertain':
            st.info('この予約は実行中の可能性があります。ここから解除・精算しません。アプリの実行状態と既存の復旧手順を確認してください。')
        else:
            _manual_form(ledger,row)
    audits=ledger.reconciliation_history()
    if audits:
        st.markdown('**手動照合の履歴（上書きなし）**')
        st.dataframe([{'リクエストID':r['request_id'],'作品UUID':r['project_id'],
            '照合結果':'無課金確認' if r['outcome']=='no_charge' else '請求額確認',
            '確定金額（円）':_money(r['actual_jpy']),'請求日・無課金確認日':r['charged_at'],
            '照合日時':r['confirmed_at']} for r in audits],hide_index=True)


def _manual_form(ledger,row):
    from manga.voice_models import digest
    identifier=row['request_id'];prefix='reconcile_'+identifier
    outcome=st.selectbox('提供元で確認できた請求結果',('未確認・反映待ち','請求額が確定','無課金が確定'),key=prefix+'_outcome')
    if outcome=='未確認・反映待ち':
        if st.session_state.get('_budget_reconcile_plan') is not None:
            st.session_state['_budget_reconcile_plan']=None
            st.session_state['_budget_reconcile_nonce']=st.session_state.get('_budget_reconcile_nonce',0)+1
        st.info('最大予約額を維持します。請求結果が確認できるまで新規送信しないでください。');return
    amount=None;charged_at=None
    if outcome=='請求額が確定':
        amount=st.text_input('このリクエストの確定請求額（円）',key=prefix+'_amount',placeholder='例：0.12（予約額ではなく確定した金額）')
        date=st.date_input('確定明細の請求日（日本時間）',value=datetime.now(ZoneInfo('Asia/Tokyo')).date(),key=prefix+'_date')
        charged_at=datetime.combine(date,time.min,tzinfo=ZoneInfo('Asia/Tokyo'))
    else:
        st.warning('「データなし」やエラー表示だけでは無課金と判断できません。この1件の無課金を提供元で確認できた場合だけ選んでください。')
    plan=digest(dict(identifier=identifier,outcome=outcome,amount=amount,date=charged_at.isoformat() if charged_at else None))
    if st.session_state.get('_budget_reconcile_plan')!=plan:
        st.session_state['_budget_reconcile_plan']=plan
        st.session_state['_budget_reconcile_nonce']=st.session_state.get('_budget_reconcile_nonce',0)+1
    consent=prefix+'_'+str(st.session_state['_budget_reconcile_nonce'])
    statement=st.checkbox('提供元の確定した請求結果を確認しました（データなし表示だけではない）',key=consent+'_statement')
    selected=st.checkbox('作品・リクエストID・金額・請求日を照合し、この1件だけに反映します',key=consent+'_selected')
    def apply():
        try:
            ledger.reconcile_manual(identifier,project_id=row['project_id'],outcome='charge' if outcome=='請求額が確定' else 'no_charge',
                actual_jpy=amount,charged_at=charged_at,confirmed_statement=statement,confirmed_request=selected)
            st.session_state['_budget_reconcile_notice']='この1件の請求照合を保存しました。診断画面の「診断の保存・請求を照合する（再送なし）」へ戻ってください。APIは送信していません。見積超過等があれば停止を継続します。'
        except (ValueError,sqlite3.Error,OSError):
            st.session_state['_budget_reconcile_error']='請求照合を保存できません。金額・請求日・確認内容・現在の状態と台帳を確認してください。自動再送・自動解除は行いません。'
    st.button('確認済みの請求結果をこの1件に反映（再送なし）',disabled=not (statement and selected),key=consent+'_apply',on_click=apply)


def _money(value):
    text = f'{value:.6f}'
    while text.endswith('0') and len(text.split('.')[1])>2:
        text = text[:-1]
    return text
