"""単発の見積→個別同意→依頼保存→二段階目の送信。初期値はすべてOFF。"""
import json

from manga.image_ai_ui import action
from manga.image_ai_live_pricing import REASON,research
from manga.ui_runtime import ui as st


def generation(service,data,prefix,plan):
    st.subheader('実API：この1枚だけ（既定OFF）')
    st.error('送信不可：'+REASON)
    enabled=st.checkbox('実APIを使用する意図と外部送信を確認する（これだけでは有効になりません）',key=prefix+'_live_enabled')
    st.caption('キーはGEMINI_API_KEY、機能フラグはMANGA_ENABLE_IMAGE_API。現在は設定しても送信を解除できません。')
    st.write(f"対象：{plan['kind']} / {plan['page'] or '人物'}:{plan['panel'] or plan['character_id']} ／ 脚本 {plan['story_version_id'][:8]} ／ {plan['options']['size']}")
    with st.expander('公式仕様・料金・未確認事項'): st.json(research(),expanded=False)
    with st.expander('計画・バイブル・プロンプト全文'): st.json(plan,expanded=False)
    consents=[]
    for ref in plan['reference_images']:
        if st.checkbox('この参照画像の外部送信に同意：'+ref['id'][:8]+' / 人物 '+ref['character_id'][:8],key=prefix+'_live_consent_'+ref['id']): consents.append(ref['id'])
        st.caption('SHA-256：'+ref['sha256'])
    if not plan['reference_images']: st.caption('参照画像なしの人物基準画像計画です。本文・人物設定は外部送信の対象です。')
    st.info('次の操作：確認済み料金カードを登録 → 最大費用を確認 → 依頼を保存 → 別操作でこの1枚を生成。今回は最大課金量が未確認のため停止します。')
    uploaded=st.file_uploader('実APIの確認済み料金カード（JSONファイル・秘密情報を含めない）',type=['json'],key=prefix+'_live_card')
    card=None
    if uploaded is not None:
        try:
            raw=uploaded.getvalue()
            if len(raw)>32768: raise ValueError
            from manga.image_ai_live_pricing import validate_card
            card=validate_card(json.loads(raw))
            st.caption('カード読込済み。自己申告のカードだけでは未確認の送信制限を解除できません。')
        except (ValueError,TypeError,KeyError,AttributeError): st.error('料金カードの形式・必須項目を確認してください。キーや任意の設定ファイルは取り込めません。')
    if st.button('共有予算の支出・予約・残額を確認',key=prefix+'_live_budget'):
        def load():
            from manga.image_ai_live_plan import shared_ledger
            summary=shared_ledger(service).summary(service.project_id)
            st.session_state[prefix+'_live_summary']={k:str(v) for k,v in summary.items() if k.endswith('_jpy')}
        action(load,'共有予算を確認しました。新規予約・API送信はありません。')
    summary=st.session_state.get(prefix+'_live_summary')
    if summary:
        st.write(f"作品：精算{summary['project_settled_jpy']}円・予約/請求不明{summary['project_reserved_jpy']}円・残額{summary['project_remaining_jpy']}円（生涯500円）")
        st.write(f"全作品月間：精算{summary['monthly_settled_jpy']}円・予約/請求不明{summary['monthly_reserved_jpy']}円・残額{summary['monthly_remaining_jpy']}円（5,000円）")
        st.caption('更新ボタンを押した時点の値です。請求書の保証額ではありません。')
    # 本番能力チェックはカード・キーから独立。fakeはテスト注入時だけ利用する。
    allowed=False
    try: service.live_adapter.check(); allowed=True
    except ValueError: pass
    if st.button('この1枚の最大費用を検証',disabled=not enabled or card is None or not allowed,key=prefix+'_live_estimate'):
        def calculate(): st.session_state[prefix+'_live_quote']=service.estimate_live(plan,card,consents)
        action(calculate,'最大額と入力ハッシュを検証しました。まだAPI送信はありません。')
    quoted=st.session_state.get(prefix+'_live_quote')
    if quoted:
        st.write('保守的な最大予約額：'+quoted['max_jpy']+'円 ／ モデル：'+quoted['card']['model'])
        if quoted['fake']: st.warning('fake transport専用の架空料金です。実費0円。実画像品質を検証した結果ではありません。')
    approved=st.checkbox('上記最大費用と参照画像を確認し、この1枚の依頼保存を承認する',key=prefix+'_live_approve')
    new=st.checkbox('同条件でも別の1枚として再見積・新規依頼する',key=prefix+'_live_new')
    if st.button('承認した実API依頼を保存（まだ送信しない）',disabled=not allowed or not enabled or not approved or not quoted or card is None,key=prefix+'_live_create'):
        action(lambda:service.create_live_job(plan,card,consents,approval_token=quoted['approval_token'],confirmed=approved,expected_revision=data['revision'],new_request=new),'1枚の依頼IDを保存しました。次の別操作で送信します。')
    jobs={j['id']:j for j in reversed(data['jobs']) if j['provider']=='gemini-live' and j['plan']['material_token']==plan['material_token']}
    selected=st.selectbox('送信対象の実API依頼',list(jobs),key=prefix+'_live_job') if jobs else None
    second=st.checkbox('保存した依頼ID・費用を再確認し、この1枚だけの送信を承認する',key=prefix+'_live_second_'+(selected or 'none'))
    if st.button('この1枚を生成',type='primary',disabled=not allowed or not enabled or not second or selected is None or jobs[selected]['state']!='queued',key=prefix+'_live_send'):
        action(lambda:service.run(selected,approved=second),'1候補の保存が完了しました。候補比較で人間が目視・権利確認してから採用してください。')


def status(job):
    fake=job['live']['fake']
    st.caption(('fake APIの模擬実行（実費0円）' if fake else '実API依頼')+' ／ request_id：'+job['request_id'])
    st.write('状態：'+job['state']+' ／ 最大予約：'+job['live']['max_jpy']+'円')
    if job['state']=='uncertain': st.warning('請求不明です。0円・無料失敗とは扱いません。共有予算画面で明細を照合してください。')
    else: st.caption(('模擬' if fake else 'usage換算')+'精算記録：'+job['cost_jpy']+'円。未精算は確定費用ではありません。')
