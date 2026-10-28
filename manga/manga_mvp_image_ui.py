"""ホームの画像設定・送信承認。通信は既存Serviceだけが実行する。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from manga.ui_runtime import ui as st
from manga.storage import StorageError


def creation_settings():
    choice = st.selectbox('一括制作の画像AI', ['mock', 'manual', 'gemini'],
        format_func=lambda v: {'mock':'無料モック（確認用画像）', 'manual':'手動画像制作（画像で停止）', 'gemini':'Gemini 3.1 Flash Lite Image（有料・承認ごと）'}[v], key='mvp_image_mode')
    cap = st.number_input('作品の最大予算（円）', min_value=1, max_value=500, value=500, key='mvp_max_budget')
    if choice != 'gemini':
        return choice, None, str(cap)
    from manga.gemini_image_contract import template, ASPECTS
    value = template()
    st.info('接続実装済み・実通信未検証です。この画像APIに無料枠はありません。初回は1枚のみ確認し、その後も対象・費用を承認した範囲だけ送信します。')
    quality = st.selectbox('画像品質・思考上限', ['minimal', 'high'],
        format_func=lambda v: {'minimal':'標準（1K・思考込み最大2,048トークン）', 'high':'高い思考上限（1K・最大4,096トークン）'}[v], key='mvp_image_quality')
    aspect = st.selectbox('画像アスペクト比', list(ASPECTS), key='mvp_image_aspect')
    fx = st.number_input('見積に使う上限為替（円/USD）', min_value=1.0, max_value=10000.0, value=150.0, key='mvp_image_fx')
    tax = st.number_input('税の倍率', min_value=1.0, max_value=10.0, value=1.10, key='mvp_image_tax')
    safety = st.number_input('為替・料金の安全係数', min_value=1.0, max_value=10.0, value=1.25, key='mvp_image_safety')
    st.caption('為替150円・税1.10・安全係数1.25は見積入力の初期値です。本人の契約・税・支払条件に合わせて確認してください。人物の完全一致や公開品質は保証しません。')
    st.markdown('[公式モデル](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite-image) / [料金](https://ai.google.dev/gemini-api/docs/pricing) / [利用条件](https://ai.google.dev/gemini-api/terms)')
    acknowledged = st.checkbox('公式モデル・最大課金量・有料アカウントの利用権限・利用規約と、プロンプト／人物設定／参照画像の外部送信条件を確認した', key='mvp_image_conditions')
    if not acknowledged:
        raise ValueError('画像APIの条件を確認してください。確認前は送信できません。')
    value.update(verified_on=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat(),
        fx_jpy_per_usd=str(fx), tax_multiplier=str(tax), safety_multiplier=str(safety),
        tax_note='利用者が上限為替・税倍率・安全係数を画面で確認',
        thinking_level=quality, max_output_tokens=2048 if quality == 'minimal' else 4096,
        max_thinking_tokens=2048 if quality == 'minimal' else 4096,
        aspect_ratio=aspect, width=ASPECTS[aspect][0], height=ASPECTS[aspect][1],
        maximum_charge_verified=True, terms_verified=True, account_access_reviewed=True, external_data_reviewed=True)
    from manga.image_ai_live_pricing import quotation
    maximum = quotation(value)['max_jpy']
    from decimal import Decimal
    st.write(f'画像1枚の最大予約額：{maximum}円 / 基準2枚＋24コマ：{Decimal(maximum) * 26}円（テキスト費用は別途共通予算へ加算）')
    st.caption('入力最大65,536・出力と思考の合計上限を最高出力単価で見積。実際の枚数と対象は脚本・人物確定後に再見積します。予算不足なら送信せず停止します。')
    return choice, value, str(cap)


def progress(engine):
    from manga.manga_mvp_images import enabled
    if not enabled(engine):
        return
    from manga.image_ai_live_plan import shared_ledger
    price = engine.images.lite_configuration()
    data = engine.images.store.load()
    summary = shared_ledger(engine.images).summary(engine.project_id)
    generated = [c for c in data['candidates'] if c['model'] == price['model']]
    st.write(f"生成済み画像：{len(generated)}枚 / 使用・予約を含む予算：{summary['project_used_jpy']}円 / 作品上限：{engine.load()['plan'].get('max_budget_jpy', '500')}円")
    st.caption('画像APIは有料です。送信失敗時の自動再送・モデル変更はありません。未確認の素材は公開品質として完了にしません。')
    if not engine.images.lite_first_confirmed(price):
        first = next((c for c in generated if any(r['candidate_id'] == c['id'] and r['sha256'] == c['sha256'] for r in data['reviews'])), None)
        if first:
            job = next(j for j in data['jobs'] if j['id'] == first['job_id'])
            with st.expander('初回1枚の保存・usage・料金', expanded=True):
                st.write(f"モデル：{first['model']} / 精算額：{job['cost_jpy']}円")
                st.json(job.get('usage', {}))
                ok = st.checkbox('初回画像・保存・精算を確認し、追加画像の見積へ進む', key='mvp_first_ok_' + first['id'])
                if st.button('初回1枚の確認を記録', disabled=not ok, key='mvp_first_confirm'):
                    engine.confirm_first_image(first['id'], confirmed=True)
                    st.rerun()
    failures = [j for j in data['jobs'] if j['provider'] == 'gemini-live' and j['state'] not in ('queued', 'ready')]
    for job in failures:
        st.warning(job.get('error') or '画像の請求・保存を確認してください。')
        if st.button('照合済み応答の保存だけを復旧（再送なし）', key='mvp_image_recover_' + job['id']):
            engine.recover_image_save(job['id'], confirmed=True)
            st.rerun()
    value = engine.image_preview()
    if not value:
        return
    # すでに承認済みの対象には新しい承認ボタンを重ねない。
    if all(engine.images.find_lite_scope(p, price) for p in value['plans']):
        return
    names = {p['id']:p['name'] for p in engine.story.artwork.load()['characters']}
    with st.container(border=True):
        st.subheader('今回の画像送信を確認')
        st.write(f"モデル：{price['model']} / {value['count']}枚 / 最大{value['max_jpy']}円")
        st.caption('保存先：' + str(engine.images.store.root / 'image_ai/candidates'))
        for p in value['plans']:
            label = '基準画像：' + names[p['character_id']] if p['kind'] == 'character' else f"{p['page']}ページ {p['panel']}コマ"
            st.write(label + f" / 参照画像 {len(p['reference_images'])}枚")
        with st.expander('外部へ送信するプロンプト・参照画像'):
            for p in value['plans']:
                st.text(p['prompt'])
                for r in p['reference_images']:
                    st.caption(f"人物：{names.get(r['character_id'], '要確認')} / 画像：{r['id']} / SHA：{r['sha256']}")
        st.caption('順番に各1回送信し、エラー・請求不明・素材変更・予算不足で停止。承認対象外の追加生成は行いません。')
        ready = True
        try:
            engine.images.live_adapter.check_model(price, for_send=True)
        except (ValueError, StorageError) as error:
            ready = False
            st.warning(str(error))
        if not value['possible']:
            st.warning('最大予約額が残予算を超えるか、請求未確定です。少ないページ数・標準品質・手動画像制作を選ぶか、共通予算で照合してください。')
        ok = st.checkbox('上記の全対象・参照画像の外部送信・最大費用と停止条件を承認する', key='mvp_image_scope_' + value['token'])
        if st.button('この範囲だけ画像生成を承認して進める', type='primary', disabled=not ok or not ready or not value['possible'], key='mvp_image_approve'):
            engine.approve_images(value['token'], confirmed=True)
            engine.start(engine.plan()['token'])
            st.rerun()
