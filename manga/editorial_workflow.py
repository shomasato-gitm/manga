"""制作状況・評価の対応関係・採用と確定の状態を、保存された事実から計算する。"""

from manga.editorial_storage import current_analysis


def version_context(version, history):
    by_id = {item["id"]: item for item in history}
    seen = set()
    while version and version["id"] not in seen:
        seen.add(version["id"])
        if version.get("revision_context"):
            return version["revision_context"]
        source = by_id.get(version.get("restored_from"))
        if source is None or source["story"] != version["story"]:
            return None
        version = source
    return None


def evaluation_for_version(data, version, history):
    original_id = version["id"]
    by_id = {item["id"]: item for item in history}
    seen = set()
    while version and version["id"] not in seen:
        seen.add(version["id"])
        record = next((item for item in reversed(data["evaluations"]) if item["payload"]["story_version_id"] == version["id"] and item["source_story"] == version["story"]), None)
        if record:
            return record, version["id"] != original_id
        source = by_id.get(version.get("restored_from"))
        if source is None or source["story"] != version["story"]:
            break
        version = source
    return None, False


def workflow_status(data, history):
    references = [item for item in data["references"] if not item["deleted_at"]]
    analyzed = sum(current_analysis(data, item) is not None for item in references)
    improved = [item for item in history if item.get("revision_context")]
    evaluated_ids = {item["payload"]["story_version_id"] for item in data["evaluations"]}
    reevaluated = sum(item["id"] in evaluated_ids for item in improved)
    final = data["finalizations"][-1] if data["finalizations"] else None
    changed = bool(final and history and final["source_story"] != history[-1]["story"])
    confirmed = bool(final and history and not changed)
    rows = [
        {"工程": "参考作品登録", "状態": f"{len(references)}作品" if references else "任意・未登録"},
        {"工程": "参考作品分析", "状態": f"現在の登録内容：{analyzed}/{len(references)}作品" if references else "任意・対象なし"},
        {"工程": "ストーリー作成", "状態": f"{len(history)}版を保存" if history else "未作成"},
        {"工程": "AI評価", "状態": f"{len(data['evaluations'])}件の評価履歴" if data["evaluations"] else "未評価"},
        {"工程": "AI改善", "状態": f"{len(improved)}版の改善履歴" if improved else "未実施"},
        {"工程": "再評価", "状態": f"改善版の再評価：{reevaluated}版" if reevaluated else "未実施"},
        {"工程": "脚本確定", "状態": "現在の内容で確定済み" if confirmed else "確定後に変更あり・要再確認" if changed else "未確定"},
    ]
    return rows, changed, confirmed
