"""1コマの確認→作画→画像登録→採用を同じ画面で完結する。合成エンジンは呼ばない。"""
from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store, cached_read
from manga.artwork_storage import ArtworkStore
from manga.assistant_storage import AssistantStore
from manga.artwork_models import panel_key
from manga.studio_workflow import panel_states
from manga.panel_workspace import neighbor, next_unfinished, overview_rows
from manga.panel_direction_ui import direction_editor
from manga.panel_assets_ui import candidates, reference_images, image_history, old_panel_history
from manga.storage import StorageError


def open_panel(project_id, page, panel, navigate):
    st.session_state[f"workspace_{project_id}_jump"] = (page, panel)
    navigate("イラスト制作", "コマ制作ワークスペース")


def _load(project, directory):
    store = ArtworkStore(directory, project.id)
    data = read_store(store)
    history = read_store(store.story_store)
    return store, data, history[-1] if history else None


def _overview(rows, prefix, jump):
    st.dataframe(overview_rows(rows), hide_index=True, use_container_width=True)
    unfinished = [r for r in rows if not r["direction"] or not r["selected"] or r["stale"]]
    target_area = st.empty()
    if unfinished:
        with target_area.container():
            by_key = {r["key"]: r for r in unfinished}
            target = st.selectbox("未完了コマへ移動", list(by_key), format_func=lambda k: f"{by_key[k]['page']}ページ・{by_key[k]['panel']}コマ / {by_key[k]['status']}" + (" / 要確認" if by_key[k]["stale"] else ""), key=f"{prefix}_unfinished")
            if st.button("このコマの画像管理を開く", key=f"{prefix}_jump"):
                row = by_key[target]
                jump(row["page"], row["panel"])
    else:
        target_area.success("すべてのコマが採用済みです。漫画ページ編集へ進めます。")


def render_panel_overview(project, directory, navigate):
    st.header("絵コンテ・全コマの制作状況")
    st.caption("作画指示はAI一括生成・確認・採用を利用できます。個別の調整から画像採用まではコマ制作ワークスペースで進めます。AI評価・改善は任意です。")
    try:
        store, data, latest = _load(project, directory)
        if not latest: st.info("先にストーリーJSONを登録してください。"); return
        rows = panel_states(latest["story"], data, store.root, latest['id'])
        _overview(rows, f"board_{project.id}", lambda p, c: open_panel(project.id, p, c, navigate))
        if st.button("コマ制作ワークスペースを開く", type="primary"):
            navigate("イラスト制作", "コマ制作ワークスペース")
    except StorageError as error:
        st.error(str(error))


def _selection(story, latest, prefix):
    key = f"{prefix}_{latest['id']}"
    jump = st.session_state.pop(f"{prefix}_jump", None)
    pages = list(range(len(story["pages"])))
    if jump:
        # ストーリーのコマ数が減っても、存在しないキーへ保存しない。
        p, c = jump
        if p-1 in pages and 0 <= c-1 < len(story["pages"][p-1]["panels"]):
            st.session_state[f"{key}_page"] = p-1
            st.session_state[f"{key}_{p-1}_panel"] = c-1
    page_index = st.selectbox("画像を確認するページ", pages, format_func=lambda n: f"{n+1}ページ", key=f"{key}_page")
    page = story["pages"][page_index]
    panel_index = st.selectbox("画像を管理するコマ", list(range(len(page["panels"]))), format_func=lambda n: f"{n+1}コマ", key=f"{key}_{page_index}_panel")
    return page, page["panels"][panel_index]


def _panel_navigation(project, latest, page, panel, rows, prefix, navigate):
    def jump(target):
        st.session_state[f"{prefix}_jump"] = target
        st.rerun()
    previous = neighbor(latest["story"], page, panel, -1)
    following = neighbor(latest["story"], page, panel, 1)
    unfinished = next_unfinished(rows, panel_key(page, panel))
    left, right, action = st.columns([1, 1, 2])
    with left:
        if st.button("← 前のコマ", disabled=previous is None): jump(previous)
    with right:
        if st.button("次のコマ →", disabled=following is None): jump(following)
    with action:
        if unfinished and unfinished != (page, panel):
            if st.button("次の未完成コマへ", type="primary"): jump(unfinished)
        elif unfinished is None:
            if st.button("漫画ページ編集へ進む", type="primary"): navigate("漫画ページ編集")
        else:
            st.caption("このコマの作画指示・採用画像を確認すると、全コマがそろいます。")
    st.caption("入力後は欄外をクリックしてから移動してください。未保存の編集はコマごとに保持し、自動保存しません。")


def render_panel_workspace(project, directory, navigate, history_only=False):
    prefix = f"workspace_{project.id}"
    st.header("コマ制作ワークスペース" if not history_only else "イラスト制作の詳細")
    notice = st.empty()
    if f"{prefix}_notice" in st.session_state:
        notice.success(st.session_state.pop(f"{prefix}_notice"))
    try:
        store, data, latest = _load(project, directory)
        if not latest:
            st.info("先にストーリーJSONを登録してください。登録後に各コマを制作できます。")
            if st.button("ストーリーの取り込みへ"): navigate("企画・ストーリー", "生成・取り込み")
            return
        # 途中の複数ファイル保存を、新しい手動保存で妨げない。
        assistant = AssistantStore(directory, project.id)
        if cached_read(assistant.journal_path, assistant.pending):
            st.warning("採用保存が途中です。先にAI一括生成・採用画面で保存を再開してください。")
            if st.button("採用保存の再開画面へ"): navigate("絵コンテ・作画指示", "AI一括生成・採用")
            return
        if history_only:
            image_history(store, data, latest, prefix)
            return
        from manga.page_storage import PageStore
        composition=read_store(PageStore(directory,project.id))
        story = latest["story"]
        rows = panel_states(story, data, store.root, latest['id'])
    except StorageError as error:
        st.error(str(error)); return

    def jump(p, c):
        st.session_state[f"{prefix}_jump"] = (p, c)
        st.rerun()

    complete = sum(r["direction"] and r["selected"] and not r["stale"] for r in rows)
    st.progress(complete / len(rows), text=f"制作済み {complete}/{len(rows)}コマ（指示と採用画像を確認済み）")
    with st.expander("全ページ・全コマの進捗と移動"):
        _overview(rows, prefix, jump)
    page, panel = _selection(story, latest, prefix)
    state = next(r for r in rows if r["key"] == panel_key(page["page"], panel["panel"]))
    st.subheader(f"{page['page']}ページ・{panel['panel']}コマ")
    if st.button('このコマを対象確認票付きの手動画像制作で開く'):
        st.session_state['manual_'+project.id+'_jump'] = (page['page'],panel['panel'])
        navigate('イラスト制作','手動画像制作')
    _panel_navigation(project, latest, page["page"], panel["panel"], rows, prefix, navigate)
    action = "以前の作画指示・画像を確認する" if state["stale"] else "作画指示を保存する" if not state["direction"] else "次の未完成コマへ進む" if state["selected"] else "画像候補を比較して採用する" if state["candidates"] else "プロンプトと基準画像をChatGPTへ渡し、生成画像を登録する"
    st.info(f"現在の状態：{state['status']}" + (" / 要確認" if state["stale"] else "") + f" ｜ 次にやること：{action}")
    if state["direction_stale"]:
        st.warning("ストーリーの変更または人物削除により、以前の作画指示は要確認です。内容を確認して保存し直してください。")
    if state["image_stale"]:
        st.warning("採用画像は以前のストーリー・作画設定、または画像ファイルが見つからない状態です。画像との一致を確認してください。")
    st.write(f"描写：{panel['description']}")
    st.caption(f"作風：{data['style']}")
    from manga.page_prompt_hints import layout_hint
    st.caption(layout_hint(page,composition,panel["panel"]).split("\n")[0])
    if (store.root / "character_ai.json").is_file() and st.button("このコマのバイブル付き統一プロンプトへ"):
        st.session_state[f"character_ai_{project.id}_jump"] = (page["page"], panel["panel"])
        navigate("キャラクター・作風", "AIキャラクター制作・一貫性管理")
    with st.expander("セリフ・ナレーション・作風を確認"):
        st.write(f"セリフ：{panel['dialogue'] or 'なし'}")
        st.write(f"ナレーション：{panel['narration'] or 'なし'}")
        st.write(f"作風：{data['style']}")
        st.caption(f"対象ストーリー版：{latest['id']}")
    entry = data["panels"].get(state["key"]) or {}
    direction = entry.get("direction") or {}

    def save(action, message):
        try:
            with operation(f"{page['page']}ページ・{panel['panel']}コマを保存しています", invalidate_actions=True):
                action()
            st.session_state[f"{prefix}_notice"] = message
            st.rerun()
        except (ValueError, StorageError) as error:
            st.error(f"{error} 入力は保持しています。最新の版・保存先・空き容量を確認してから再試行してください。")

    st.subheader("1. 作画指示を確認・調整")
    dirty = direction_editor(store, data, latest, page["page"], panel, direction, state, prefix, save)
    st.subheader("2. プロンプトと基準画像を取得")
    prompt_slot = st.empty()
    if dirty:
        prompt_slot.warning("表示・取得するプロンプトは保存済みの作画指示を使います。変更を使う場合は先に保存してください。")
    try:
        from manga.image_prompts import panel_image_prompt
        from manga.prompt_ui import prompt_tools
        prompt = panel_image_prompt(project, story, page, panel, data, composition)
        with st.empty().container():
            prompt_tools(prompt, f"{prefix}_{latest['id']}_{state['key']}_prompt", f"image_p{page['page']}_c{panel['panel']}.txt", response_json=False)
    except ValueError as error:
        prompt_slot.info(str(error) + " 上の作画指示を保存すると取得できます。")
    with st.expander("登場人物の設定・基準画像（確認・ダウンロード）"):
        people = [c for c in data["characters"] if c["id"] in direction.get("character_ids", [])]
        for character in people:
            st.write(f"{character['name']}：髪型 {character['hairstyle']} / 髪色 {character['hair_color']} / 目 {character['eye_color']} / 服装 {character['clothing']}")
        reference_images(store, data, direction, f"{prefix}_refs")
    candidates(store, data, latest, page["page"], panel, entry, prefix, save, dirty or state["direction_stale"])
    old_panel_history(store, data, story, prefix)
