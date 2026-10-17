"""現在のコマだけの参照画像、候補比較・採用。元画像や過去の候補は変更しない。"""
from manga.ui_runtime import ui as st
from manga.artwork_models import image_context_changed, panel_key
from manga.artwork_ui import _gallery, _show_image, _upload_bytes
from manga.gallery_ui import thumbnail
from manga.storage import StorageError


def reference_images(store, data, direction, prefix):
    characters = [c for c in data["characters"] if c["id"] in direction.get("character_ids", []) and not c["deleted_at"]]
    if not characters:
        st.caption("登場人物を作画指示に指定すると、その人物の基準画像をここで確認できます。")
        return
    st.caption("必要な画像をダウンロードし、ChatGPTへ手動で添付してください。画像を自動送信しません。")
    for character in characters:
        key = f"{prefix}_{character['id']}"
        images = {im["id"]: im for im in character["references"]}
        if not images:
            st.info(f"{character['name']}：参照画像が未登録です。キャラクター・作風の「基準画像・作風」で登録できます。")
            continue
        selected = st.selectbox(f"{character['name']}の参照画像", list(images), index=list(images).index(character["primary_reference_id"]),
                     format_func=lambda k, images=images, primary=character["primary_reference_id"]: images[k]["view"] + ("（基準画像）" if k == primary else "") + " / " + images[k]["original_name"], key=f"{key}_reference")
        image = images[selected]
        try:
            st.image(thumbnail(store, image), caption=f"{character['name']} / {image['view']}", width=240)
            if st.checkbox(f"{character['name']}の元画像を取得", key=f"{key}_{selected}_get"):
                # ダウンロード用の原寸は明示時だけ読む。縮小画像用キャッシュとは分離する。
                from manga.panel_downloads import original_bytes
                st.download_button(f"{character['name']}の参照画像をダウンロード", original_bytes(store, image), file_name=f"{image['id']}.png", mime="image/png", key=f"{key}_{selected}_download")
        except StorageError as error:
            st.warning(str(error))


def candidates(store, data, latest, page, panel, entry, prefix, save, dirty):
    key = f"{prefix}_{data['revision']}_{page}_{panel['panel']}"
    st.subheader("3. 生成画像を登録・比較・採用")
    st.caption("必須：ChatGPTで作った画像をこのコマへ登録し、採用を選びます。候補・過去の採用履歴は保持します。")
    upload = st.file_uploader("このコマの候補画像", type=["png", "jpg", "jpeg", "webp"], key=f"{key}_upload")
    st.caption("PNG・JPEG・WebPの静止画、20MB・2400万画素以下。保存先は現在のページ・コマです。")
    if st.button("候補画像を保存", type="primary", key=f"{key}_save", disabled=dirty):
        save(lambda: store.add_panel_image(page, panel["panel"], _upload_bytes(upload), upload.name,
             story_version_id=latest["id"], expected_revision=data["revision"]), "候補画像を保存しました。下の候補を比較し、採用してください。")
    area = st.empty()
    if not entry.get("candidates"):
        area.info("画像が未登録です。プロンプトをChatGPTへ手動送信して作った画像を選んでください。")
        return
    with area.container():
        images = {im["id"]: im for im in entry["candidates"]}
        ids = list(images)
        # 比較と採用の操作を画像一覧より前に置く。
        selected = st.selectbox("採用候補を選ぶ", ids, index=ids.index(entry["selected_image_id"]) if entry["selected_image_id"] else len(ids)-1,
                    format_func=lambda k: f"候補{ids.index(k)+1} / {images[k]['original_name']}" + ("（採用中）" if k == entry["selected_image_id"] else ""), key=f"{key}_select")
        image = images[selected]
        if image["source_panel"] != panel:
            st.warning("選択中の画像は以前のストーリー内容で登録されています。画像と新しい脚本の一致を確認してください。")
        if image_context_changed(image, entry, data):
            st.warning("画像登録後に作画指示・作風・人物設定が変わっています。画像との一致を確認してください。")
        if st.button("選んだ候補を採用", type="primary", disabled=dirty or selected == entry["selected_image_id"], key=f"{key}_adopt"):
            save(lambda: store.select_panel_image(page, panel["panel"], selected, expected_revision=data["revision"], story_version_id=latest["id"]),
                 "採用画像を保存しました。上部から次の未完成コマへ進めます。以前の候補も残っています。")
        if st.checkbox("採用候補の元画像を取得", key=f"{prefix}_{selected}_original"):
            from manga.panel_downloads import original_bytes
            try:
                st.download_button("候補画像をダウンロード", original_bytes(store, image), file_name=f"{image['id']}.png", mime="image/png", key=f"{prefix}_{selected}_download")
            except StorageError as error:
                st.warning(str(error))
        _gallery(store, entry, f"{prefix}_{page}_{panel['panel']}_candidate", panel, current_artwork=data, allow_download=False)
        compare_area = st.empty()
        if len(ids) > 1:
            with compare_area.container():
                if st.checkbox("2候補を並べて比較", key=f"{key}_compare"):
                    other = st.selectbox("比較する候補", [k for k in ids if k != selected], format_func=lambda k: f"候補{ids.index(k)+1} / {images[k]['original_name']}", key=f"{key}_other")
                    for column, candidate in zip(st.columns(2), (image, images[other])):
                        with column:
                            st.image(thumbnail(store, candidate), caption=candidate["original_name"], use_container_width=True)


def image_history(store, data, latest, prefix):
    st.header("画像履歴")
    st.caption("現在のコマの候補・採用はコマ制作ワークスペースで管理します。ここでは全コマの採用画像と、脚本から外れたコマの履歴を確認できます。")
    pages = latest["story"]["pages"]
    number = st.selectbox("採用画像を確認するページ", [p["page"] for p in pages], key=f"{prefix}_history_page")
    columns = st.columns(3)
    for i, panel in enumerate(pages[number-1]["panels"]):
        entry = data["panels"].get(panel_key(number, panel["panel"])) or {}
        image = next((im for im in entry.get("candidates", []) if im["id"] == entry.get("selected_image_id")), None)
        with columns[i % 3]:
            st.write(f"{number}ページ・{panel['panel']}コマ")
            if image: _show_image(store, image, "採用画像", f"{prefix}_history_{image['id']}")
            else: st.info("未採用")
    old_panel_history(store, data, latest["story"], prefix)


def old_panel_history(store, data, story, prefix):
    keys = {panel_key(p["page"], c["panel"]) for p in story["pages"] for c in p["panels"]}
    entries = {k: e for k, e in data["panels"].items() if k not in keys and e["candidates"]}
    area = st.empty()
    if entries:
        with area.container():
            with st.expander("現在のストーリーにないコマの画像履歴"):
                old = st.selectbox("過去のコマ", list(entries), key=f"{prefix}_old_panel")
                if st.checkbox("過去の候補画像を表示", key=f"{prefix}_old_images"):
                    _gallery(store, entries[old], f"{prefix}_old", inside_expander=True)
