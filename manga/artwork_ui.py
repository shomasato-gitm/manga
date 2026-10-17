"""キャラクター設定、作画指示、ローカル画像管理のStreamlit画面。"""

import streamlit as st

from manga.artwork_models import CHARACTER_ALL_FIELDS as CHARACTER_FIELDS, DIRECTION_FIELDS, image_context_changed, panel_key
from manga.artwork_storage import ArtworkStore
from manga.image_prompts import character_image_prompt, panel_image_prompt
from manga.storage import StorageError


def _perform(action, message, notice_key, select_key=None):
    try:
        result = action()
        if select_key:
            st.session_state[f"{select_key}_pending"] = None if result["deleted_at"] else result["id"]
        st.session_state[notice_key] = message
        st.rerun()
    except (ValueError, StorageError) as error:
        st.error(str(error))


def _show_image(store, image, caption, key):
    try:
        content = store.image_bytes(image)
        st.image(content, caption=caption, use_container_width=True)
        st.download_button("画像をダウンロード", content, file_name=f"{image['id']}.png", mime="image/png", key=key)
    except StorageError as error:
        st.warning(str(error))


def _upload_bytes(upload):
    if upload is None:
        raise ValueError("保存する画像ファイルを選択してください。")
    return upload.getvalue()


def render_artwork_workspace(project, directory):
    st.divider()
    st.header("キャラクター・作画・画像管理")
    st.caption("ChatGPTでの生成は手動で行います。プロンプト枠の右上からコピーし、参照画像もご自身で添付してください。")
    prefix = f"art_{project.id}"
    notice_key = f"{prefix}_notice"
    if notice_key in st.session_state:
        st.success(st.session_state.pop(notice_key))
    store = ArtworkStore(directory, project.id)
    try:
        data = store.load()
    except StorageError as error:
        st.error(str(error))
        return
    with st.form(f"{prefix}_{data['revision']}_style_form"):
        style = st.text_area("プロジェクトの作風", value=data["style"], max_chars=2000)
        save_style = st.form_submit_button("作風を保存")
    if save_style:
        _perform(lambda: store.save_style(style, expected_revision=data["revision"]), "作風を保存しました。", notice_key)
    st.caption("設定やコマを切り替える前に保存してください。プロンプトは保存済みの設定から作成します。")
    character_tab, direction_tab, image_tab = st.tabs(["キャラクター設定", "コマの作画指示", "生成画像・採用一覧"])
    with character_tab:
        _characters(store, data, prefix, notice_key)
    try:
        history = store.story_store.load_history()
    except StorageError as error:
        with direction_tab:
            st.error(str(error))
        with image_tab:
            st.error(str(error))
        return
    if not history:
        with direction_tab:
            st.info("第2段階のJSON取り込みでストーリーを保存すると、コマごとに作画指示を登録できます。")
        with image_tab:
            st.info("ストーリーを保存すると、各コマの候補画像を登録できます。")
        return
    latest = history[-1]
    with direction_tab:
        _directions(project, store, data, latest, prefix, notice_key)
    with image_tab:
        _panel_images(store, data, latest, prefix, notice_key)


def _characters(store, data, prefix, notice_key):
    active = {item["id"]: item for item in data["characters"] if not item["deleted_at"]}
    select_key = f"{prefix}_character"
    if f"{select_key}_pending" in st.session_state:
        st.session_state[select_key] = st.session_state.pop(f"{select_key}_pending")
    selected_id = st.selectbox("編集するキャラクター", [None] + list(active),
                               format_func=lambda value: "新規キャラクター" if value is None else active[value]["name"], key=select_key)
    character = active.get(selected_id)
    key = f"{prefix}_{data['revision']}_{selected_id or 'new'}"
    with st.form(f"{key}_character_form"):
        values = {}
        for field, label in CHARACTER_FIELDS.items():
            value = character.get(field, "") if character else ""
            if field in ("clothing", "personality", "features"):
                values[field] = st.text_area(label, value=value, max_chars=2000, key=f"{key}_{field}")
            else:
                values[field] = st.text_input(label, value=value, max_chars=100 if field == "name" else 2000, key=f"{key}_{field}")
        submitted = st.form_submit_button("キャラクターを保存")
    if submitted:
        _perform(lambda: store.save_character(values, character_id=selected_id, expected_revision=data["revision"]),
                 "キャラクターを保存しました。", notice_key, select_key)
    if not character:
        return
    with st.expander("キャラクターを削除"):
        confirmed = st.checkbox(f"「{character['name']}」をキャラクター一覧から削除する", key=f"{key}_delete_confirm")
        st.caption("参照画像と過去の画像候補は保持します。この人物を使う作画指示は、削除後に選び直してください。")
        if st.button("このキャラクターを削除", disabled=not confirmed, key=f"{key}_delete"):
            _perform(lambda: store.delete_character(selected_id, expected_revision=data["revision"]),
                     "キャラクターを一覧から削除しました。", notice_key, select_key)

    st.subheader("キャラクター基準画像の生成準備")
    view = st.selectbox("生成する向き・種類", ["正面", "斜め", "全身", "表情差分"], key=f"{key}_generate_view")
    expression = st.text_input("生成する表情", value="自然な表情", max_chars=200, key=f"{key}_generate_expression")
    st.code(character_image_prompt(character, data["style"], view, expression), language="text", wrap_lines=True)
    st.caption("右上のコピーボタンでコピーできます。参照画像は下からダウンロードし、ChatGPTへ手動で添付してください。")

    st.subheader("参照画像の登録")
    upload = st.file_uploader("キャラクターの参照画像", type=["png", "jpg", "jpeg", "webp"], key=f"{key}_reference_upload")
    ref_view = st.selectbox("参照画像の種類", ["正面", "斜め", "全身", "表情差分", "その他"], key=f"{key}_ref_view")
    note = st.text_input("参照画像のメモ", max_chars=500, key=f"{key}_ref_note")
    st.caption("PNG・JPEG・WebPの静止画、20MB・2400万画素以下。保存時にPNGへ変換します。")
    if st.button("参照画像を保存", key=f"{key}_save_reference"):
        _perform(lambda: store.add_reference(selected_id, _upload_bytes(upload), upload.name, ref_view, note, expected_revision=data["revision"]),
                 "参照画像を保存しました。", notice_key)
    references = character["references"]
    if references:
        by_id = {image["id"]: image for image in references}
        primary = st.selectbox("基準にする参照画像", list(by_id), index=list(by_id).index(character["primary_reference_id"]),
                               format_func=lambda value: f"{by_id[value]['view']} / {by_id[value]['original_name']} / {by_id[value]['saved_at']}", key=f"{key}_primary")
        if st.button("基準画像を選択", key=f"{key}_select_primary"):
            _perform(lambda: store.select_reference(selected_id, primary, expected_revision=data["revision"]), "基準画像を変更しました。", notice_key)
        columns = st.columns(3)
        for index, image in enumerate(references):
            with columns[index % 3]:
                badge = "（基準画像）" if image["id"] == character["primary_reference_id"] else ""
                _show_image(store, image, f"{image['view']}{badge} {image['note']}", f"{prefix}_reference_{image['id']}")


def _choose_panel(story, prefix, page_label, panel_label):
    page_index = st.selectbox(page_label, list(range(len(story["pages"]))), format_func=lambda value: f"{value + 1}ページ", key=f"{prefix}_page")
    page = story["pages"][page_index]
    panel_index = st.selectbox(panel_label, list(range(len(page["panels"]))), format_func=lambda value: f"{value + 1}コマ", key=f"{prefix}_{page_index}_panel")
    return page, page["panels"][panel_index]


def _directions(project, store, data, latest, prefix, notice_key):
    story = latest["story"]
    page, panel = _choose_panel(story, f"{prefix}_{latest['id']}_direction", "作画指示のページ", "作画指示のコマ")
    key = f"{prefix}_{data['revision']}_{page['page']}_{panel['panel']}"
    st.write(f"描写：{panel['description']}")
    st.write(f"セリフ：{panel['dialogue'] or 'なし'}")
    st.write(f"ナレーション：{panel['narration'] or 'なし'}")
    entry = data["panels"].get(panel_key(page["page"], panel["panel"]))
    direction = entry["direction"] if entry and entry["direction"] else {}
    if direction and direction["source_panel"] != panel:
        st.warning("このコマのストーリーが変更されています。以前の作画指示を確認して保存し直してください。")
    by_id = {item["id"]: item for item in data["characters"]}
    options = [value for value, item in by_id.items() if not item["deleted_at"] or value in direction.get("character_ids", [])]
    with st.form(f"{key}_direction_form"):
        ids = st.multiselect("登場キャラクター", options, default=direction.get("character_ids", []),
                             format_func=lambda value: by_id[value]["name"] + ("（削除済み）" if by_id[value]["deleted_at"] else ""), key=f"{key}_people")
        fields = {"character_ids": ids}
        for field, label in DIRECTION_FIELDS.items():
            fields[field] = st.text_area(label, value=direction.get(field, ""), max_chars=2000, key=f"{key}_{field}")
        submitted = st.form_submit_button("このコマの作画指示を保存")
    if submitted:
        _perform(lambda: store.save_direction(page["page"], panel["panel"], fields, story_version_id=latest["id"], expected_revision=data["revision"]),
                 "作画指示をストーリーとは別に保存しました。", notice_key)
    if direction:
        st.subheader("このコマの画像生成プロンプト")
        try:
            prompt = panel_image_prompt(project, story, page, panel, data)
            st.code(prompt, language="text", wrap_lines=True)
            st.caption("右上からコピーし、参照画像もChatGPTへ手動で添付してください。画像中にはセリフ・吹き出しを描かない指示です。")
        except ValueError as error:
            st.warning(str(error))


def _gallery(store, entry, prefix, current_panel=None, inside_expander=False, current_artwork=None):
    columns = st.columns(3)
    for index, image in enumerate(entry["candidates"]):
        with columns[index % 3]:
            badge = "（採用）" if image["id"] == entry["selected_image_id"] else ""
            _show_image(store, image, f"候補{index + 1}{badge} {image['original_name']}", f"{prefix}_{image['id']}")
            if current_panel is not None and image["source_panel"] != current_panel:
                st.warning("以前のストーリー内容の候補です。")
            if current_artwork is not None and image_context_changed(image, entry, current_artwork):
                st.warning("画像登録後に作画指示・作風・人物設定が変わっています。画像との一致を確認してください。")
            conditions = st.container() if inside_expander else st.expander("登録時の作画条件")
            with conditions:
                if inside_expander:
                    st.caption("登録時の作画条件")
                st.write(f"描写：{image['source_panel'].get('description', '')}")
                st.write(f"作風：{image['context'].get('style', '')}")
                direction = image["context"].get("direction") or {}
                for field, label in DIRECTION_FIELDS.items():
                    if direction.get(field):
                        st.write(f"{label}：{direction[field]}")
                names = [character["name"] for character in image["context"].get("characters", [])]
                st.write("人物：" + ("、".join(names) or "未指定"))


def _panel_images(store, data, latest, prefix, notice_key):
    story = latest["story"]
    page, panel = _choose_panel(story, f"{prefix}_{latest['id']}_images", "画像を確認するページ", "画像を管理するコマ")
    key = f"{prefix}_{data['revision']}_{page['page']}_{panel['panel']}_images"
    st.write(f"描写：{panel['description']}")
    st.caption("ChatGPTで画像を生成し、ダウンロードした画像をこのコマへ登録してください。過去の候補は削除しません。")
    upload = st.file_uploader("このコマの候補画像", type=["png", "jpg", "jpeg", "webp"], key=f"{key}_upload")
    if st.button("候補画像を保存", key=f"{key}_save"):
        _perform(lambda: store.add_panel_image(page["page"], panel["panel"], _upload_bytes(upload), upload.name,
                                              story_version_id=latest["id"], expected_revision=data["revision"]),
                 "候補画像を保存しました。採用する画像を選んでください。", notice_key)
    entry = data["panels"].get(panel_key(page["page"], panel["panel"]))
    if entry and entry["candidates"]:
        _gallery(store, entry, f"{prefix}_candidate", panel, current_artwork=data)
        by_id = {image["id"]: image for image in entry["candidates"]}
        ids = list(by_id)
        selected = st.selectbox("採用候補を選ぶ", ids, index=ids.index(entry["selected_image_id"]) if entry["selected_image_id"] else 0,
                                format_func=lambda value: f"候補{ids.index(value) + 1} / {by_id[value]['original_name']}", key=f"{key}_select")
        if by_id[selected]["source_panel"] != panel:
            st.warning("選択中の画像は以前のストーリー内容で登録されています。内容を確認して採用してください。")
        if st.button("選んだ候補を採用", disabled=selected == entry["selected_image_id"], key=f"{key}_adopt"):
            _perform(lambda: store.select_panel_image(page["page"], panel["panel"], selected, expected_revision=data["revision"]), "採用画像を変更しました。以前の候補も残っています。", notice_key)
    st.subheader(f"{page['page']}ページの採用画像一覧")
    columns = st.columns(3)
    for index, current in enumerate(page["panels"]):
        with columns[index % 3]:
            item = data["panels"].get(panel_key(page["page"], current["panel"]))
            selected_image = next((image for image in item["candidates"] if image["id"] == item["selected_image_id"]), None) if item else None
            st.write(f"{current['panel']}コマ目")
            if selected_image:
                _show_image(store, selected_image, "採用画像", f"{prefix}_overview_{selected_image['id']}")
                if selected_image["source_panel"] != current:
                    st.warning("ストーリー変更前の画像です。")
            else:
                st.info("未採用")
    current_keys = {panel_key(item["page"], p["panel"]) for item in story["pages"] for p in item["panels"]}
    old_entries = {value: item for value, item in data["panels"].items() if value not in current_keys and item["candidates"]}
    if old_entries:
        with st.expander("現在のストーリーにないコマの画像履歴"):
            old = st.selectbox("過去のコマ", list(old_entries), format_func=lambda value: value.replace(":", "ページ・") + "コマ", key=f"{prefix}_old_panel")
            _gallery(store, old_entries[old], f"{prefix}_old", inside_expander=True)
