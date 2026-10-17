"""提案の一覧・実差分・1件編集・選択採用。確定前の操作は提案だけを更新する。"""

import copy
import json

import streamlit as st

from manga.artwork_models import CHARACTER_ALL_FIELDS, DIRECTION_FIELDS, panel_key
from manga.assistant_changes import differences, display_differences, item_before, name_key, reference_values
from manga.assistant_models import REFERENCE_AI_FIELDS
from manga.editorial_models import REFERENCE_FIELDS


def item_label(kind, item):
    if kind in ("directions", "improvement"):
        return f"{item['page']}ページ・{item['panel']}コマ"
    return item["fields"]["name" if kind == "characters" else "title"]


def after_fields(kind, item):
    return reference_values(item) if kind == "references" else item["fields"]


def _edit(kind, item, request, key):
    changed = copy.deepcopy(item)
    with st.form(f"{key}_edit_form"):
        if kind == "characters":
            by_id = {c["id"]: c for c in request["context"]["artwork"]["characters"] if not c["deleted_at"]}
            ids = [None] + list(by_id)
            changed["existing_id"] = st.selectbox("反映先の人物（新規または既存ID）", ids, index=ids.index(item["existing_id"]), format_func=lambda value: f"{by_id[value]['name']} / {value}" if value else "新規人物", key=f"{key}_existing")
            labels = CHARACTER_ALL_FIELDS
        elif kind in ("directions", "improvement"):
            by_id = {c["id"]: c for c in request["context"]["artwork"]["characters"] if not c["deleted_at"]}
            changed["fields"]["character_ids"] = st.multiselect("提案の登場人物", list(by_id), default=item["fields"]["character_ids"], format_func=lambda value: by_id[value]["name"], key=f"{key}_people")
            labels = DIRECTION_FIELDS
        else:
            labels = REFERENCE_FIELDS
        for name, label in labels.items():
            changed["fields"][name] = st.text_area(f"提案：{label}", value=item["fields"][name], max_chars=100 if name == "name" else 2000, key=f"{key}_{name}")
        if kind == "references":
            for name, label in REFERENCE_AI_FIELDS.items():
                if name == "confirmation_status":
                    changed[name] = st.selectbox("提案：確認状態", ("要確認", "ユーザー確認済み"), index=("要確認", "ユーザー確認済み").index(item[name]), key=f"{key}_{name}")
                else:
                    changed[name] = st.text_area(f"提案：{label}", value=item[name], max_chars=2000, key=f"{key}_{name}")
        if kind in ("directions", "improvement"):
            changed["reason"] = st.text_area("提案：演出・改善理由", value=item["reason"], max_chars=4000, key=f"{key}_reason")
        q = st.text_area("提案：確認事項（1行1件）", value="\n".join(item["questions"]), max_chars=10000, key=f"{key}_questions")
        changed["questions"] = [line.strip() for line in q.splitlines() if line.strip()]
        submitted = st.form_submit_button("この提案の編集を保存")
    if kind == "characters":
        for name, value in changed["fields"].items():
            if value != item["fields"].get(name):
                changed["provenance"][name] = dict(status="ユーザー編集", evidence="アプリの確認・編集画面で修正")
    return submitted, changed


def review_proposal(store, data, proposal, request, artwork, editorial, key, run):
    kind = request["kind"]
    items = proposal["payload"]["items"]
    labels = {item["id"]: item_label(kind, item) for item in items}
    st.subheader("提案一覧と既存内容の比較")
    rows = []
    for item in items:
        before, after = item_before(kind, item, artwork, editorial), after_fields(kind, item)
        rows.append({"対象": labels[item["id"]], "状態": proposal["decisions"][item["id"]], "既存値": json.dumps(before, ensure_ascii=False),
                     "提案値": json.dumps(after, ensure_ascii=False), "変更項目": "、".join(d["field"] for d in differences(before, after)),
                     "理由・確認事項": " / ".join(filter(None, [item.get("reason", ""), *item["questions"]]))})
    st.dataframe(rows, use_container_width=True, hide_index=True)
    pending = [item["id"] for item in items if proposal["decisions"][item["id"]] == "確認待ち"]
    pending_area = st.empty()
    if pending:
        with pending_area.container():
            if st.session_state.get(f"{key}_item") not in pending:
                st.session_state[f"{key}_item"] = pending[0]
            selected = st.selectbox("確認・編集する提案", pending, format_func=lambda value: labels[value], key=f"{key}_item")
            item = next(value for value in items if value["id"] == selected)
            question_slot = st.empty()
            if item["questions"]:
                question_slot.warning("確認が必要です：" + " / ".join(item["questions"]))
            before, after = item_before(kind, item, artwork, editorial), after_fields(kind, item)
            st.table(display_differences(differences(before, after)) or [{"field": "変更なし", "before": "", "after": ""}])
            if kind == "characters":
                st.table([dict(項目=CHARACTER_ALL_FIELDS[name], 区分=value["status"], 根拠=value["evidence"]) for name, value in item["provenance"].items()])
                duplicate = [c for c in artwork["characters"] if not c["deleted_at"] and c["id"] != item["existing_id"] and name_key(c["name"]) == name_key(item["fields"]["name"])]
                duplicate_slot = st.empty()
                if duplicate:
                    duplicate_slot.warning("既存人物との重複候補：" + "、".join(f"{c['name']} / {c['id']}" for c in duplicate))
            if kind == "references":
                st.info(f"作品情報：{item['confirmation_status']}。根拠：{item['basis']}。確認状態はユーザーの判断で変更してください。")
            with st.expander("選んだ提案だけ編集"):
                submitted, changed = _edit(kind, item, request, f"{key}_{selected}_{data['revision']}")
                if submitted:
                    run(lambda: store.edit_item(proposal["id"], selected, changed, expected_revision=data["revision"]), "提案を編集しました。既存設定は変更していません。")
            image_warning = st.empty()
            if kind in ("directions", "improvement"):
                affected = [labels[item["id"]] for item in items if item["id"] in pending
                            and (artwork["panels"].get(panel_key(item["page"], item["panel"])) or {}).get("selected_image_id")
                            and differences(item_before(kind, item, artwork, editorial), item["fields"])]
                if affected:
                    image_warning.warning("採用画像があるコマの指示変更です。画像と指示が一致しなくなる可能性があります：" + "、".join(affected))
            all_selected = st.checkbox("確認待ちをまとめて選択", key=f"{key}_all")
            selected_ids = []
            columns = st.columns(3)
            # 部分採用後も同じ位置に状態を残し、前のチェック欄を残さない。
            max_items = max(len(p["payload"].get("items", [])) for p in data["proposals"] if p["request_id"] == proposal["request_id"])
            for index in range(max_items):
                if index >= len(items):
                    columns[index % 3].empty()
                    continue
                item_id = items[index]["id"]
                available = item_id in pending
                suffix = "" if available else f"（{proposal['decisions'][item_id]}）"
                checked = columns[index % 3].checkbox(f"採用候補：{labels[item_id]}{suffix}", value=all_selected if available else False, disabled=not available, key=f"{key}_{data['revision']}_{all_selected}_{item_id}_check")
                if checked and available:
                    selected_ids.append(item_id)
            duplicate_ok = False
            if kind in ("characters", "references"):
                duplicate_ok = st.checkbox("同名候補がある場合も別の人物・作品として登録する", key=f"{key}_duplicate")
            approved = st.checkbox("確認事項と差分を確認し、選択した提案の保存を承認する", key=f"{key}_{data['revision']}_approval")
            if st.button("選択した提案を採用", disabled=not approved or not selected_ids, key=f"{key}_adopt"):
                run(lambda: store.adopt(proposal["id"], selected_ids, expected_revision=data["revision"], expected_artwork_revision=artwork["revision"], expected_editorial_revision=editorial["revision"], allow_duplicates=duplicate_ok), "選択した提案だけを採用しました。画像と未採用の設定は保持しています。")
            if st.button("選択した提案を見送る", disabled=not selected_ids, key=f"{key}_reject"):
                run(lambda: store.decide(proposal["id"], selected_ids, "見送り", expected_revision=data["revision"]), "選択した提案を見送りました。既存設定は変更していません。")
    else:
        pending_area.info("確認待ちの提案はありません。採用済み・見送りの状態、原案と編集履歴は保持しています。")
    rejected = [item_id for item_id, status in proposal["decisions"].items() if status == "見送り"]
    rejected_area = st.empty()
    if rejected:
        with rejected_area.container():
            restore = st.multiselect("確認待ちに戻す見送り提案", rejected, format_func=lambda value: labels[value], key=f"{key}_restore")
            if st.button("見送りを確認待ちに戻す", disabled=not restore, key=f"{key}_restore_button"):
                run(lambda: store.decide(proposal["id"], restore, "確認待ち", expected_revision=data["revision"]), "確認待ちに戻しました。")
    with st.expander("提案の原案・編集履歴・採用記録"):
        st.json(dict(original=proposal["original_payload"], edits=proposal["edits"], adoptions=[item for item in data["adoptions"] if item["proposal_id"] == proposal["id"]]))
