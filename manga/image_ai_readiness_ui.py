"""モデル調査の確認済み/未確認と送信禁止理由を既存画面に表示する。"""
import json

from manga.image_ai_readiness import diagnostic
from manga.ui_runtime import ui as st


def render(prefix):
    report=diagnostic()
    st.subheader('本番接続の準備状況（Phase 2-D3）')
    st.error('送信禁止：公式資料から最大総課金額を確定できていません。キー・カード・支出制限だけでは解除できません。')
    st.caption('確認日：'+report['verified_on']+' ／ 第一候補：'+report['model']+' ／ 初回対象：1024×1024・画像1枚')
    st.write('機能フラグ：'+('設定済み' if report['feature_flag_enabled'] else 'OFF')+' ／ 認証：'+('設定済み（値は表示しません）' if report['authentication_configured'] else '未設定（今回の無料検証には不要）'))
    st.dataframe([dict(未確認項目=r['title'],必要な根拠=r['needed']) for r in report['blockers']],hide_index=True,use_container_width=True)
    ref=report['reference_cost']
    st.info('画像1枚の参考費用：Vertex Globalの画像出力部分のみ $'+ref['usd']+' ≈ '+str(round(float(ref['jpy']),2))+'円（1 USD='+ref['fx_jpy_per_usd']+'円・税係数'+ref['tax_multiplier']+'）。Developer APIの総額・最大額ではありません。最大費用は未確定です。')
    st.caption('Google Cloudのalerts-only予算は自動的なハード上限ではありません。spend capも対象サービス・報告遅延を確認する補助策です。')
    st.download_button('本番接続の確認事項をJSONでダウンロード',json.dumps(report,ensure_ascii=False,indent=2),file_name='gemini_image_readiness.json',mime='application/json',key=prefix+'_readiness_download')
    with st.expander('公式資料から確認できた事実・取得状況'):
        for fact in report['facts']: st.write(fact['statement'])
        st.dataframe([dict(資料=s['id'],対象サービス=s['scope'],取得='成功' if s['retrieved'] else '接続制限で未取得',URL=s['url']) for s in report['sources']],hide_index=True,use_container_width=True)
    with st.expander('残作業・送信できない場合の代替案'):
        st.write('公式Developer資料を取得できる環境で、モデル別の最大課金量・参照・usage・契約条件を確認し、根拠付きの別PRで能力判定を更新します。その後も少額1枚の実呼び出しへの別途承認と、対象ごとの確認が必要です。')
        st.write('現在は既存の無料モック、またはChatGPT/AI Studioで手動作成した画像の取り込みを利用できます。月額UI契約をAPI無料枠とは扱いません。Vertexへの変更も別サービスの再設計・別PRが必要で、自動切り替えは行いません。')
