"""保存済みの作画評価の表示と、評価時点の実際の設定差分。"""

from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store

from manga.artwork_models import direction_fields, panel_key
from manga.assistant_changes import differences, display_differences
from manga.assistant_models import DRAWING_CRITERIA
from manga.editorial_models import SUBJECTIVE_NOTICE


def evaluations(data):
    requests = {item["id"]: item for item in data["requests"]}
    return [item for item in data["proposals"] if requests[item["request_id"]]["kind"] == "evaluation"]


def evaluation_report(data, proposal, prefix):
    st.caption(SUBJECTIVE_NOTICE)
    payload = proposal["payload"]
    st.write(payload["summary"])
    st.metric("作画の総合参考点（100点換算）", round(sum(item["score"] for item in payload["scores"].values()) / len(DRAWING_CRITERIA) * 10, 1))
    st.table([dict(観点=label, 点数=payload["scores"][key]["score"], 良い点=payload["scores"][key]["good"], 問題点=payload["scores"][key]["problem"],
                   理由=payload["scores"][key]["reason"], 改善案=payload["scores"][key]["suggestion"], 対象=str(payload["scores"][key]["targets"])) for key, label in DRAWING_CRITERIA.items()])
    others = {item["id"]: item for item in evaluations(data) if item["id"] != proposal["id"]}
    if not others:
        st.info("改善後に新しい評価依頼を作り、評価JSONを取り込むと評価点と作画指示を比較できます。")
        return
    with st.expander("改善前後の作画評価・指示を比較"):
        comparison = st.selectbox("比較元の作画評価", list(reversed(others)), format_func=lambda key: f"{others[key]['saved_at']} / {key[:8]}", key=f"{prefix}_compare")
        before = others[comparison]
        st.table([dict(観点=label, 比較元=before["payload"]["scores"][key]["score"], 表示中=payload["scores"][key]["score"], 差=payload["scores"][key]["score"] - before["payload"]["scores"][key]["score"]) for key, label in DRAWING_CRITERIA.items()])
        requests = {item["id"]: item for item in data["requests"]}
        left, right = requests[before["request_id"]], requests[proposal["request_id"]]
        rows = []
        for target in right["targets"]:
            key = panel_key(target["page"], target["panel"])
            a = (left["context"]["artwork"]["panels"].get(key) or {}).get("direction")
            b = (right["context"]["artwork"]["panels"].get(key) or {}).get("direction")
            rows.extend(dict(対象=key, **diff) for diff in differences(direction_fields(a) if a else {}, direction_fields(b) if b else {}))
        st.dataframe(display_differences(rows) or [{"対象": "比較対象内の指示変更なし"}], use_container_width=True)
        if left["story_version_id"] != right["story_version_id"] or left["targets"] != right["targets"]:
            st.warning("ストーリー版または評価対象が異なります。採点の前提が違うため単純な優劣として扱わないでください。")
        st.caption("差分は各評価依頼時に保存したJSONの実際の値です。改善理由は提案・採用履歴から確認できます。")
