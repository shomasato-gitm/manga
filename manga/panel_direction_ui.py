"""選択中コマの指示編集。保存は明示操作のみ。下書きのキーに画像の版を含めない。"""
from manga.ui_runtime import ui as st
from manga.artwork_models import DIRECTION_FIELDS, direction_fields
from manga.panel_workspace import draft_token


def direction_editor(store, data, latest, page, panel, direction, state, prefix, save):
    token = f"{prefix}_{draft_token(latest['id'], page, panel['panel'], direction)}"
    by_id = {c["id"]: c for c in data["characters"]}
    options = [k for k, c in by_id.items() if not c["deleted_at"] or k in direction.get("character_ids", [])]
    drafts = st.session_state.setdefault("_panel_direction_drafts", {})
    draft_key = (prefix, page, panel["panel"])
    previous = drafts.get(draft_key)
    previous_area = st.empty()
    if previous and previous["token"] != token and previous["dirty"]:
        with previous_area.container():
            st.warning("ストーリー版または確定指示が更新されたため、以前の未保存入力は自動適用しません。下書きを取得し、最新の内容と確認してください。")
            import json
            st.download_button("以前の未保存入力をJSONで取得", json.dumps(previous, ensure_ascii=False, indent=2), file_name=f"direction_draft_p{page}_c{panel['panel']}.json", mime="application/json", key=f"{token}_previous_draft")
    editing = st.checkbox("作画指示を編集", value=not direction or state["direction_stale"], key=f"{token}_editing")
    area = st.empty()
    if not editing:
        with area.container():
            st.write("登場人物：" + ("、".join(by_id[k]["name"] for k in direction.get("character_ids", [])) or "未指定"))
            st.write(" / ".join(f"{label}：{direction[field]}" for field, label in DIRECTION_FIELDS.items() if direction.get(field)))
        # 折りたたんだ編集欄にも下書きが残りうる。
        return bool(previous and previous["token"] == token and previous["dirty"])
    with area.container():
        st.caption("任意項目は空欄でも保存できます。保存後にプロンプトへ反映します。切り替えでは自動保存しません。")
        with st.form(f"{token}_form"):
            fields = {"character_ids": st.multiselect("登場キャラクター", options, default=direction.get("character_ids", []),
                       format_func=lambda k: by_id[k]["name"] + ("（削除済み）" if by_id[k]["deleted_at"] else ""), key=f"{token}_people")}
            columns = st.columns(2)
            for i, (field, label) in enumerate(DIRECTION_FIELDS.items()):
                with columns[i % 2]:
                    fields[field] = st.text_area(label, value=direction.get(field, ""), max_chars=2000, height=80, key=f"{token}_{field}")
            dirty = direction_fields(fields) != direction_fields(direction)
            if not previous or previous["token"] == token or dirty:
                drafts[draft_key] = dict(token=token, story_version_id=latest["id"], page=page, panel=panel["panel"], fields=fields, dirty=dirty)
            if dirty:
                st.warning("未保存の作画指示があります。画像登録・採用の前に保存してください。移動してもこのコマの下書きを保持します。")
            if state["selected"] and dirty:
                st.warning("採用画像があるコマの指示変更です。保存後に画像と指示の一致を確認してください。")
            submitted = st.form_submit_button("このコマの作画指示を保存", type="primary")
        if submitted:
            def persist():
                result = store.save_direction(page, panel["panel"], fields, story_version_id=latest["id"], expected_revision=data["revision"])
                drafts.pop(draft_key, None)
                return result
            save(persist, "作画指示を保存しました。この画面でプロンプトを取得し、イラスト制作を続けられます。")
    return dirty
