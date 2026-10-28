"""ホーム内の一括制作。既存の制作画面・正本・個別の送信承認を再利用。"""
from uuid import uuid4

from manga.ui_runtime import ui as st, operation
from manga.storage import StorageError
from manga.manga_mvp import MangaMVP, creation_plan, create
from manga.studio_storage import StudioStore


def render_manga_mvp(project, directory, navigate):
    try:
        active = project and StudioStore(directory, project.id).load().get('manga_mvp')
    except StorageError as error:
        st.error(str(error))
        return False
    if active:
        st.header(project.title)
        _progress(MangaMVP(directory, project.id), navigate)
    with st.expander('テーマから新しい漫画を制作', expanded=not bool(project)):
        _create(project, directory, navigate)
    return bool(active)


def _create(project, directory, navigate):
    from manga.story_ai_storage import AIStoryStore
    st.caption('新規作品に企画・脚本・人物設定を保存します。既存作品は上書きしません。動画は今回の完成条件に含めません。')
    if project and st.button('別の新規作品として制作する準備', key='mvp_new_nonce'):
        st.session_state['mvp_creation_nonce'] = str(uuid4())
        st.rerun()
    theme = st.text_area('漫画のテーマ', max_chars=2000, key='mvp_theme')
    genre = st.text_input('漫画のジャンル', value='青春', max_chars=50, key='mvp_genre')
    count = st.number_input('制作するページ数', min_value=1, max_value=100, value=8, step=1, key='mvp_pages')
    style = st.text_area('漫画の画風', value='白黒の日本漫画。読みやすい線画と整理された背景。', max_chars=2000, key='mvp_style')
    mode = st.selectbox('一括制作のテキストAI', ['mock', 'verified-free', 'paid'],
        format_func=lambda value: {'mock':'無料モック（動作確認用のテスト画像）', 'verified-free':'Gemini 3.5（本人確認済み無料枠）', 'paid':'Gemini 3.5（有料・個別見積）'}[value], key='mvp_mode')
    with st.container(border=True):
        st.caption('画像APIの出力単価を比較')
        from manga.manga_mvp_costs import image_comparison
        st.dataframe([{'モデル':r['model'], '1枚 USD':r['per_image_usd'], '24コマ USD':r['panels_usd'], '基準2枚を含む26枚 USD':r['total_usd']} for r in image_comparison()], hide_index=True)
        st.caption('2026-10-28確認。出力部分だけの比較で、最大請求額ではありません。入力・参照画像・思考・税は別。画像AIを選んでも、条件確認・送信対象と最大料金の承認まで送信しません。日本漫画の実品質は未検証です。')
    nonce = st.session_state.setdefault('mvp_creation_nonce', str(uuid4()))
    try:
        from manga.manga_mvp_image_ui import creation_settings
        image_mode, image_config, cap = creation_settings()
        card = AIStoryStore(directory, project.id).load()['card'] if project and mode != 'mock' else None
        plan = creation_plan(directory, dict(theme=theme, genre=genre, page_count=int(count), style=style), nonce, mode=mode, card=card, image_config=image_config, max_budget=cap, image_mode=image_mode)
    except (ValueError, OSError, StorageError) as error:
        st.caption(str(error))
        if mode != 'mock' and st.button('既存のGemini接続・料金設定を開く'):
            navigate('企画・ストーリー', 'AI企画・ストーリー')
        return
    st.write(f"テキストAPI：最大{plan['text_calls']}送信、1回最大{plan['per_call_max_jpy']}円、累計最大{plan['text_max_jpy']}円")
    st.caption('順序：企画3案 → 先頭案の脚本 → 制作脚本の保存・確定 → 人物設定 → 基準画像 → 作画指示・コマ画像 → セリフ合成 → PNG・PDF・ZIP。実APIの評価・自動改善は追加送信しません。')
    st.caption('今回のテキスト送信範囲：入力テーマ・ジャンル・ページ数、生成した企画・脚本、人物抽出と設定テキスト。画像は後で対象・参照画像・料金を個別に承認するまで送信しません。')
    st.caption('保存先：' + plan['destination'])
    if mode != 'mock':
        st.caption('作品累計500円・全作品月間5,000円の共通予算を使用。画像費用も共通管理し、無料テキスト枠にも有料相当額を予約します。')
        st.write(f"作品残予算：{plan['budget']['project_remaining_jpy']}円 / 全作品の月間残予算：{plan['budget']['monthly_remaining_jpy']}円")
        st.caption('モデル：' + plan['card']['model'] + ' / 料金確認日：' + plan['card']['verified_on'])
        st.caption('最大入力は既存モデルの全入力幅、最大出力は保存済み料金JSONの上限（思考込み）。検索・キャッシュ・追加ツールは利用しません。')
        if plan['budget']['blocked']:
            st.warning('請求確認待ち等のため送信停止中です。既存の共通予算画面で照合してください。')
    approved = st.checkbox('上記の外部送信・最大費用と、新規作品の企画・脚本・一意な新規人物の制作採用を承認する（公開品質は未確認）', key='mvp_approval_' + plan['token'])
    if st.button('漫画を制作', type='primary', disabled=not approved or bool(plan.get('budget', {}).get('blocked'))):
        try:
            with operation('新規作品の制作を開始しています', invalidate_actions=True):
                saved = create(directory, plan, approved=True)
                from app import set_form
                set_form(saved)
                engine = MangaMVP(directory, saved.id)
                engine.start(engine.plan()['token'])
            navigate('ホーム・進行状況')
        except (ValueError, OSError, StorageError) as error:
            st.error(str(error))


def _progress(engine, navigate):
    with st.empty().container():
        _progress_content(engine, navigate)


def _progress_content(engine, navigate):
    from manga.manga_mvp_steps import waiting_images
    from manga.manga_mvp import LABELS
    from manga.image_ai_models import REVIEW_FIELDS
    areas = {key:st.empty() for key in ('header', 'review', 'images', 'actions', 'links', 'history', 'outputs')}
    try:
        plan = engine.plan()
        run = engine.load()
        active = False
        try:
            with engine.automation.lock():
                pass
        except StorageError:
            active = True
        with areas['header'].container():
            st.subheader('漫画をまとめて制作')
            st.write('状態：' + ('処理中' if active else plan['status']))
            if run['plan']['mode'] == 'mock':
                st.warning('無料モックのテスト作品です。画像は本物の人物一致・公開品質を保証しません。')
            if plan['reason'] != 'outputs_ready' and plan['reason']:
                st.info(plan['reason'])
            if plan['action']:
                st.write('次の処理：' + plan['action']['label'])
            art = engine.story.artwork.load()
            history = engine.story.stories.load_history()
            total = sum(len(p['panels']) for p in history[-1]['story']['pages']) if history else 0
            adopted = sum(bool(p['selected_image_id']) for p in art['panels'].values())
            st.progress(adopted / total if total else 0, text=f'採用済みコマ {adopted}/{total}')
            if st.button('制作状態を更新', key='mvp_refresh'):
                st.rerun()
        if active:
            with areas['actions'].container():
                _watch(engine)
            return
        if plan['changed']:
            with areas['actions'].container():
                st.warning('保存済み素材が変更されました。再検査するまで一括処理を止めています。')
                if st.button('変更した素材を検査して再開を準備', key='mvp_reinspect'):
                    engine.reinspect(plan['token'], confirmed=True)
                    st.rerun()
        else:
            candidates = waiting_images(engine)
            if candidates:
                with areas['review'].container():
                    st.write(f'確認する画像：{len(candidates)}件')
                    jobs = {j['id']:j for j in engine.images.store.load()['jobs']}
                    names = {c['id']:c['name'] for c in art['characters']}
                    for offset in range(0, len(candidates), 3):
                        for column, candidate in zip(st.columns(3), candidates[offset:offset + 3]):
                            with column:
                                target = jobs[candidate['job_id']]['plan']
                                label = '基準画像：' + names[target['character_id']] if target['kind'] == 'character' else f"{target['page']}ページ {target['panel']}コマ"
                                st.image(engine.images.candidate_bytes(candidate), caption=('テスト画像 / ' if candidate['mock'] else '') + label)
                    checks = {key:st.checkbox(label, key='mvp_review_' + plan['token'] + key) for key, label in REVIEW_FIELDS.items()}
                    note = st.text_area('画像の確認メモ・権利の根拠', key='mvp_review_note_' + plan['token'], max_chars=2000)
                    if st.button('全対象の画像確認を記録（まだ採用しない）', disabled=not all(checks.values()) or not note.strip(), key='mvp_review'):
                        engine.review_images(plan['token'], checks, note)
                        st.rerun()
            with areas['images'].container():
                from manga.manga_mvp_image_ui import progress
                progress(engine)
            with areas['actions'].container():
                if st.button('確認済みの範囲で制作を再開', type='primary', disabled=not bool(plan['action']), key='mvp_resume'):
                    engine.start(plan['token'])
                    st.rerun()
        with areas['links'].container():
            if st.button('既存の手動画像制作・採用を開く', key='mvp_manual'):
                navigate('イラスト制作', '手動画像制作')
            if st.button('共通予算・請求照合を開く', key='mvp_budget'):
                navigate('完成・書き出し', 'API予算管理')
        events = engine.load()['events']
        if events:
            with areas['history'].container():
                with st.expander('制作の実行履歴'):
                    st.dataframe([{'処理':LABELS[e['kind']], '状態':e['state'], '開始':e['started_at']} for e in events[-100:]], hide_index=True)
        outputs = {(e['kind'], tuple(e['pages'])):e for e in engine.studio.load()['exports'] if e.get('artifact_role') == 'manga'}
        if outputs:
            with areas['outputs'].container():
                st.subheader('制作結果をまとめて確認')
                st.caption('PNG・PDFの出力と、人物一致・セリフ・権利・公開品質の確認は別です。動画は不要です。')
                for event in outputs.values():
                    payload = engine.studio.export_bytes(event)
                    if event['kind'] == 'page':
                        with st.expander(event['filename']):
                            st.image(payload)
                    st.download_button(event['filename'] + 'を取得', payload, file_name=event['filename'], key='mvp_download_' + event['sha256'] + event['kind'])
    except (ValueError, OSError, StorageError) as error:
        st.error(str(error))


@st.fragment(run_every=3)
def _watch(engine):
    """実行中だけを自動更新。確認フォームは通常画面に置き、入力を保持する。"""
    try:
        with engine.automation.lock():
            finished = True
    except StorageError:
        finished = False
    if finished:
        st.rerun()
    events = engine.load()['events']
    st.caption(f'実行済みの処理：{sum(e["state"] == "done" for e in events)}件。状態は自動更新します。')
    if st.button('現在の処理が終わったら停止', key='mvp_stop'):
        engine.stop()
        st.info('現在の処理の完了後に停止します。送信中の予約は解除しません。')
