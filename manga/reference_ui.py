"""参考作品の追加・編集・削除と、手動で受け取る分析JSONの管理。"""

from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store

from manga.editorial_models import ANALYSIS_ASPECTS, REFERENCE_FIELDS
from manga.editorial_prompts import analysis_prompt
from manga.editorial_storage import current_analysis
from manga.editorial_ui_common import input_text, json_input, perform


def reference_management(store, data, prefix, notice_key):
    st.caption("作品の本文やセリフの大量コピーではなく、ご自身の短い要約・観察・抽象的な技法を登録してください。URLへのアクセスやスクレイピングは行いません。")
    active = {item["id"]: item for item in data["references"] if not item["deleted_at"]}
    select_key = f"{prefix}_reference"
    if f"{select_key}_pending" in st.session_state:
        st.session_state[select_key] = st.session_state.pop(f"{select_key}_pending")
    reference_id = st.selectbox("編集する参考作品", [None] + list(active), format_func=lambda value: active[value]["title"] if value else "新規参考作品", key=select_key)
    reference = active.get(reference_id)
    with st.form(f"{prefix}_{data['revision']}_{reference_id}_reference_form"):
        fields = {}
        for key, label in REFERENCE_FIELDS.items():
            value = reference[key] if reference else ""
            if key in ("title", "medium", "genre", "source_url"):
                fields[key] = st.text_input(label, value=value, max_chars=2000, key=f"{prefix}_{data['revision']}_{reference_id}_{key}")
            else:
                fields[key] = st.text_area(label, value=value, max_chars=2000, key=f"{prefix}_{data['revision']}_{reference_id}_{key}")
        submitted = st.form_submit_button("参考作品を保存")
    if submitted:
        perform(lambda: store.save_reference(fields, reference_id=reference_id, expected_revision=data["revision"]), "参考作品を保存しました。", notice_key, select_key)
    reference_controls=st.empty()
    if reference:
        with reference_controls.container():
            st.caption("作品名・媒体は必須です。編集後は新しい登録内容を分析し直してください。過去の分析は保持します。")
            confirm = st.checkbox("この参考作品を一覧から削除する", key=f"{prefix}_{reference_id}_delete_confirm")
            if st.button("参考作品を削除", disabled=not confirm, key=f"{prefix}_delete_reference"):
                perform(lambda: store.delete_reference(reference_id, expected_revision=data["revision"]), "参考作品を一覧から削除しました。過去の分析は残しています。", notice_key, select_key)


def reference_analysis(store, data, prefix, notice_key):
    active = {item["id"]: item for item in data["references"] if not item["deleted_at"]}
    if not active:
        st.info("参考作品を登録すると、分析用プロンプトを作成できます。参考作品の利用は任意です。")
        return
    reference_id = st.selectbox("分析する参考作品", list(active), format_func=lambda value: active[value]["title"], key=f"{prefix}_analysis_reference")
    reference = active[reference_id]
    st.caption("右上からコピーしてChatGPTへ貼り付け、分析JSONを戻してください。情報が不足する項目は「不明」とするプロンプトです。")
    st.code(analysis_prompt(reference), language="text", wrap_lines=True)
    values = json_input("参考作品の分析JSON", f"{prefix}_{reference_id}_analysis")
    if st.button("分析JSONを検証して保存", key=f"{prefix}_save_analysis"):
        perform(lambda: store.save_analysis(reference_id, input_text(values), expected_revision=data["revision"]), "分析メモを新しい履歴として保存しました。", notice_key)
    analyses = [item for item in data["analyses"] if item["payload"]["reference_id"] == reference_id]
    if analyses:
        by_id = {item["id"]: item for item in analyses}
        selected_id = st.selectbox("分析メモの履歴", list(reversed(by_id)), format_func=lambda value: f"{by_id[value]['saved_at']} / 登録内容v{by_id[value]['reference_snapshot']['version']}", key=f"{prefix}_{reference_id}_analysis_history")
        selected = by_id[selected_id]
        if selected["reference_snapshot"]["version"] != reference["version"]:
            st.warning("この分析は編集前の登録内容に基づきます。生成プロンプトには現在の登録内容に対応する分析だけを使います。")
        st.table([{"分析項目": label, "分析": item["analysis"], "抽象的な技法": item["abstract_technique"],
                   "根拠": item["basis"], "確認状態": item["status"]}
                  for key, label in ANALYSIS_ASPECTS.items() for item in [selected["payload"]["aspects"][key]]])
        st.write("不明点：" + selected["payload"]["unknowns"])
    if current_analysis(data, reference) is None:
        st.caption("現在の登録内容の分析は未保存です。生成時は登録した「参考にしたいストーリー技法」を利用できます。")
