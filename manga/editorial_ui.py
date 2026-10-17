"""手動の評価→改善→再評価と、JSON差分比較・採用・脚本確定。"""

import json

import streamlit as st

from manga.editorial_models import CRITERIA, MODES, SUBJECTIVE_NOTICE, normalize_targets
from manga.editorial_prompts import evaluation_prompt
from manga.editorial_ui_common import input_text, json_input, perform
from manga.editorial_workflow import evaluation_for_version, version_context, workflow_status
from manga.prompts import improvement_prompt
from manga.story_comparison import compare_stories


def render_workflow(data, history):
    st.subheader("制作ワークフロー")
    st.caption("参考作品登録 → 参考作品分析 → ストーリー作成 → AI評価 → AI改善 → 再評価 → 脚本確定（参考作品は任意）")
    rows, changed, _ = workflow_status(data, history)
    st.table(rows)
    if changed:
        st.warning("脚本確定後に内容が変更されています。既存の画像・作画指示と不整合が起こりうるため、各コマを確認し、必要なら再作画して脚本を確定し直してください。")


def _version_labels(history):
    return {version["id"]: f"v{index} ｜ {version['saved_at']} ｜ {'改善' if version.get('revision_context') else version['action']}"
            for index, version in enumerate(history, 1)}


def _locations(targets):
    return "、".join(f"{target['page']}ページ" + (f"・{target['panel']}コマ" if target["panel"] else "全体") for target in targets) or "全体"


def _evaluation_details(payload):
    st.metric("総合評価（100点換算）", f"{payload['total_score']:g} / 100")
    st.caption("総合点は8項目の合計 ÷ 80 × 100をアプリが計算します。")
    st.write("総評：" + payload["summary"])
    st.table([{
        "評価項目": label, "点数 / 10": item["score"], "良い点": item["good_points"], "問題点": item["problems"],
        "改善理由": item["reason"], "対象": _locations(item["targets"]), "具体的な改善案": item["suggestion"],
    } for key, label in CRITERIA.items() for item in [payload["criteria"][key]]])


def evaluation_and_improvement(project, store, data, history, characters, prefix, notice_key):
    st.info(SUBJECTIVE_NOTICE)
    if not history:
        st.info("ストーリーを保存すると、評価・改善・再評価ができます。")
        return
    if characters is None:
        st.error("キャラクター設定を読み込めないため、設定を維持する評価・改善プロンプトは作成できません。作画設定のエラーを確認してください。")
        return
    labels = _version_labels(history)
    version_id = st.selectbox("評価・改善する版", list(reversed(labels)), format_func=labels.get, key=f"{prefix}_{history[-1]['id']}_evaluation_version")
    version = next(item for item in history if item["id"] == version_id)
    st.caption("評価は選択した版に紐づけます。改善後は新しい版を再評価してください。右上のコピーでプロンプトをChatGPTへ手動で渡します。")
    st.subheader("ストーリー評価用プロンプト")
    st.code(evaluation_prompt(project, version, characters), language="text", wrap_lines=True)
    values = json_input("ストーリー評価JSON", f"{prefix}_{version_id}_evaluation")
    if st.button("評価JSONを検証して保存", key=f"{prefix}_save_evaluation"):
        perform(lambda: store.save_evaluation(version_id, input_text(values), expected_revision=data["revision"]), "対象版の評価を新しい履歴として保存しました。", notice_key)
    evaluations = [item for item in data["evaluations"] if item["payload"]["story_version_id"] == version_id and item["source_story"] == version["story"]]
    if not evaluations:
        st.info("この版の評価JSONを保存すると、評価に基づく改善プロンプトを作成できます。")
        return
    by_id = {item["id"]: item for item in evaluations}
    evaluation_id = st.selectbox("使用する評価履歴", list(reversed(by_id)), format_func=lambda value: f"{by_id[value]['saved_at']} / {by_id[value]['payload']['total_score']:g}点", key=f"{prefix}_{version_id}_evaluation_history")
    evaluation = by_id[evaluation_id]
    _evaluation_details(evaluation["payload"])
    st.subheader("評価に基づく改善")
    mode = st.radio("改善方式", MODES, key=f"{prefix}_{version_id}_mode")
    criteria = st.multiselect("改善したい評価項目", list(CRITERIA), default=list(CRITERIA), format_func=CRITERIA.get, key=f"{prefix}_{evaluation_id}_criteria")
    targets = []
    if mode == MODES[1]:
        choices = {}
        for page in version["story"]["pages"]:
            choices[f"{page['page']}ページ全体"] = {"page": page["page"], "panel": None}
            for panel in page["panels"]:
                choices[f"{page['page']}ページ・{panel['panel']}コマ"] = {"page": page["page"], "panel": panel["panel"]}
        selected = st.multiselect("改善するページ・コマ", list(choices), key=f"{prefix}_{evaluation_id}_targets")
        targets = [choices[value] for value in selected]
    request = st.text_area("改善してほしい点（任意）", max_chars=2000, key=f"{prefix}_{evaluation_id}_request")
    default_reason = "\n".join(f"{CRITERIA[key]}：{evaluation['payload']['criteria'][key]['reason']}" for key in criteria)
    reason = st.text_area("今回の改善理由・メモ", value=default_reason[:4000], max_chars=4000, key=f"{prefix}_{evaluation_id}_{mode}_{'-'.join(criteria)}_reason")
    if not criteria or (mode == MODES[1] and not targets):
        st.info("改善項目と、部分改善の場合は対象ページ・コマを選んでください。")
        return
    targets = normalize_targets(targets, version["story"], required=mode == MODES[1])
    st.code(improvement_prompt(project, version["story"], request, evaluation=evaluation["payload"], mode=mode, criteria=criteria, targets=targets, characters=characters), language="text", wrap_lines=True)
    st.caption("改善版も全ページを含む既存のストーリーJSONで受け取ります。以下から保存すると、改善元・評価・理由を履歴に記録します。通常のJSON取り込みも利用できますが、改善との対応は記録されません。")
    improved_values = json_input("改善版ストーリーJSON", f"{prefix}_{evaluation_id}_{mode}_improved")
    if st.button("改善版を検証して新しい版に保存", key=f"{prefix}_save_improved"):
        perform(lambda: store.save_improvement(input_text(improved_values), version_id, evaluation_id, mode, criteria, targets, reason,
                                               expected_version_id=history[-1]["id"]), "改善版を保存しました。この新しい版の評価JSONを取り込み、必要なら改善を繰り返してください。", notice_key)


def compare_and_finalize(store, data, history, prefix, notice_key):
    st.caption(SUBJECTIVE_NOTICE)
    if not history:
        st.info("ストーリーを保存するとバージョン比較・採用・脚本確定ができます。")
        return
    labels = _version_labels(history)
    version_rows = []
    for version in history:
        evaluation, inherited = evaluation_for_version(data, version, history)
        context = version_context(version, history)
        version_rows.append({"版": labels[version["id"]], "総合評価": evaluation["payload"]["total_score"] if evaluation else None,
                             "評価の対応": "同一JSONの採用元版の評価" if inherited else "この版の評価" if evaluation else "未評価",
                             "改善理由": context["reason"] if context else ""})
    st.table(version_rows)
    before_id = st.selectbox("比較元の版", list(labels), format_func=labels.get, key=f"{prefix}_compare_before")
    after_id = st.selectbox("比較先の版", list(labels), index=len(history) - 1, format_func=labels.get, key=f"{prefix}_{history[-1]['id']}_compare_after")
    before = next(item for item in history if item["id"] == before_id)
    after = next(item for item in history if item["id"] == after_id)
    old_eval, old_inherited = evaluation_for_version(data, before, history)
    new_eval, new_inherited = evaluation_for_version(data, after, history)
    score_rows = []
    for key, label in CRITERIA.items():
        old_score = old_eval["payload"]["criteria"][key]["score"] if old_eval else None
        new_score = new_eval["payload"]["criteria"][key]["score"] if new_eval else None
        score_rows.append({"評価項目": label, "比較元": old_score, "比較先": new_score,
                           "差（比較先−比較元）": round(new_score - old_score, 2) if old_score is not None and new_score is not None else None})
    st.table(score_rows)
    st.caption("未評価は空欄です。採用による同一JSONのコピーには元版の評価を明記して表示します。再評価したことにはしません。各版に複数の評価がある場合は最新を使います。")
    context = version_context(after, history)
    if context:
        st.write("比較先の改善理由：" + context["reason"])
    changes = compare_stories(before["story"], after["story"])
    fields = {"title": "タイトル", "summary": "全体のあらすじ", "page_summary": "ページ概要", "description": "描写", "dialogue": "セリフ", "narration": "ナレーション", "page": "ページの追加・削除", "panel": "コマの追加・削除"}
    st.write(f"実際のJSON差分：{len(changes)}項目")
    if changes:
        def display(value):
            return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else "（なし）" if value is None else value

        st.table([{"ページ": str(change["page"]) if change["page"] else "全体", "コマ": str(change["panel"]) if change["panel"] else "全体",
                   "項目": fields[change["field"]], "変更前": display(change["before"]), "変更後": display(change["after"])} for change in changes])
    else:
        st.info("選択した2版のストーリーJSONは同じ内容です。")
    selected = st.selectbox("採用する脚本の版", list(labels), index=len(history) - 1, format_func=labels.get, key=f"{prefix}_{history[-1]['id']}_adopt")
    st.caption("過去版を採用すると、同じ内容を新しい最新履歴として保存します。元の版や評価、画像は削除しません。未保存の編集内容は先に保存してください。")
    if st.button("この版を脚本として採用", disabled=selected == history[-1]["id"], key=f"{prefix}_adopt_button"):
        perform(lambda: store.stories.restore(selected, expected_version_id=history[-1]["id"]), "選択した版を新しい履歴として採用しました。", notice_key)
    _, _, confirmed = workflow_status(data, history)
    if st.button("現在の脚本を確定", disabled=confirmed, key=f"{prefix}_finalize"):
        perform(lambda: store.finalize(expected_version_id=history[-1]["id"], expected_revision=data["revision"]), "現在の脚本を確定しました。確定後に変更した場合は画像・作画指示も確認してください。", notice_key)
