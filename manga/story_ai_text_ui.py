"""単発送信の開示・設定。表示だけで接続・認証テストは行わない。"""
import json
from manga.ui_runtime import ui as st
from manga.story_ai_ui import action
from manga.story_ai_gemini import template
from manga.gemini_models import DEFAULT_MODEL,MODELS,get_model


def gemini_configuration(service,data,config,prefix):
    with st.expander('Geminiの接続・無料枠・料金設定'):
        saved=data.get('card') or {};initial=saved.get('model') if saved.get('model') in MODELS else DEFAULT_MODEL
        model=st.selectbox('Geminiモデル（設定・診断）',list(MODELS),index=list(MODELS).index(initial),
            format_func=lambda m:m+('（履歴・互換用）' if get_model(m).legacy else '（推奨）'),key=prefix+'_gemini_model')
        profile=get_model(model)
        st.caption('モデルを選んだだけでは保存済み設定・履歴を変更しません。対応する料金JSONを確認・保存してから通常生成に使用します。')
        if saved.get('provider')=='gemini-text':st.write('保存済みの実行モデル：'+get_model(saved['model']).id)
        if profile.legacy:st.warning('2.5 Flash-Liteは履歴・互換用です。404や利用不可でも3.5へ自動切替しません。3.5は別の料金・思考設定を確認して保存してください。')
        else:st.info('3.5はthinkingLevel=minimalを使用します。思考OFFではなく、思考トークンも出力上限・費用に含まれます。')
        st.caption('無料枠でも有料単価の最大額を共有予算へ保守的に予約・記録します。無料枠を消費する接続試験は自動実行しません。')
        st.markdown(f'[モデル]({profile.model_url}) ｜ [料金](https://ai.google.dev/gemini-api/docs/pricing) ｜ [規約・データ利用](https://ai.google.dev/gemini-api/terms)')
        st.write(f'確認したStandard参考単価：入力 ${profile.input_rate} / 出力（思考分含む） ${profile.output_rate}（各100万tokens）。公式確認日：{profile.verified_on}')
        st.download_button('Gemini料金設定のひな形を取得',json.dumps(template(model),ensure_ascii=False,indent=2),file_name=model+'-pricing.json',mime='application/json')
        from manga.editorial_ui_common import json_input,input_text
        from manga.story import load_json_payload
        raw=json_input('Gemini利用条件・料金JSON',prefix+'_gemini_card')
        from manga.gemini_diagnostics_ui import initial_diagnostic
        initial_diagnostic(service,data,raw,prefix,selected_model=model)
        checked=st.checkbox('公式モデル・アカウント利用権限・送信範囲・料金を確認した',key=prefix+'_gemini_verified')
        if st.button('Gemini利用条件・料金を保存',disabled=not checked):
            def save():
                value=load_json_payload(input_text(raw))
                if not isinstance(value,dict) or value.get('model')!=model:raise ValueError('画面で選択したモデルと料金JSONのmodelを一致させてください。保存済み設定は変更していません。')
                service.configure(config,value,expected_revision=data['revision'])
            action(save,'Gemini設定を保存しました。送信には別途1回ごとの承認が必要です。')


def disclosure(card,phase):
    st.write(f"今回：{phase} ／ モデル：{card['model']} ／ 最大入力：{card['max_input_tokens']:,} tokens ／ 最大出力：{card['max_output_tokens']:,} tokens")
    st.caption('送る情報：作品名・企画条件、登録人物の設定テキスト、選択した参考技法、選択企画、評価・改善では対象候補の全文と評価。画像・音声ファイル・APIキーは本文に含めません。')
    st.caption('追加課金機能：ツール・検索・外部キャッシュ・画像・音声・バッチは使用しません。出力は1候補。最大入力はトークンの推測ではなくモデルの全入力幅で見積もります。')
    if card['provider']=='gemini-text':st.caption('思考設定：'+get_model(card['model']).thinking_label)
    st.warning('無料APIも有料相当の保守的な予算枠を使います。請求書の金額ではありません。無料枠終了時の有料切替・モデル変更・自動リトライは行いません。')


def next_call(service,data,job,prefix):
    st.info('実APIは1送信で停止します。次の評価・改善も別の送信です。')
    if job['state'] not in ('stopped','paused') or job['phase']=='done':return
    try:
        estimate=service.next_estimate(job['id'])
        disclosure(job['card'],estimate['phase'])
        from manga.budget.config import open_budget
        summary=open_budget(service.directory).summary(service.project_id)
        st.write(f"次の1送信の最大見積：{estimate['max_jpy']}円 ／ 作品残額：{summary['project_remaining_jpy']}円 ／ 月間残額：{summary['monthly_remaining_jpy']}円")
        accepted=st.checkbox('この1送信だけの費用・送信内容を承認する',key=prefix+'_'+estimate['approval_key']+'_next_consent')
        if st.button('承認した次の1送信だけを実行',disabled=not accepted or not service.api_enabled(job.get('access_mode','paid'))):
            def start():service.approve_next(job['id'],approval=estimate['approval_key'],expected_revision=data['revision']);service.start(job['id'])
            action(start,'1送信を受け付けました。生成結果を確認してください。')
    except ValueError as error:st.info(str(error))
