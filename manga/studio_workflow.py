"""保存済みの事実から工程と各コマの状況を判定する。"""

from pathlib import Path
from manga.artwork_models import image_context_changed, panel_key
from manga.assistant_changes import active_characters


def panel_states(story, artwork, root=None, version_id=None):
    rows = []
    for page in story["pages"]:
        for panel in page["panels"]:
            key = panel_key(page["page"], panel["panel"])
            entry = artwork["panels"].get(key) or {}
            direction = entry.get("direction")
            image = next((c for c in entry.get("candidates", []) if c["id"] == entry.get("selected_image_id")), None)
            state = "作画指示未設定" if not direction else "画像未登録" if not entry.get("candidates") else "候補あり" if image is None else "採用済み"
            direction_stale=bool(direction and direction["source_panel"] != panel)
            image_stale=bool(image and (image["source_panel"] != panel or image_context_changed(image, entry, artwork)))
            if version_id is not None:
                direction_stale |= bool(direction and direction['story_version_id'] != version_id)
                image_stale |= bool(image and image['story_version_id'] != version_id)
            if image and root is not None:
                path=Path(root)/image["path"]
                image_stale=image_stale or not path.resolve().is_relative_to(Path(root).resolve()) or not path.is_file()
            deleted = {c["id"] for c in artwork["characters"] if c["deleted_at"]}
            direction_stale=direction_stale or bool(direction and set(direction["character_ids"]) & deleted)
            rows.append(dict(page=page["page"], panel=panel["panel"], key=key, status=state, stale=direction_stale or image_stale, direction_stale=direction_stale, image_stale=image_stale, direction=bool(direction), selected=bool(image), candidates=len(entry.get("candidates", []))))
    return rows


def progress(project, history, artwork, assistant, composition, studio, asset_token, root=None):
    story = history[-1]["story"] if history else None
    version_id = history[-1]["id"] if history else None
    panels = panel_states(story, artwork, root) if story else []
    characters = [c for c in artwork["characters"] if not c["deleted_at"]]
    requests = {r["id"]:r for r in assistant["requests"]}
    pending = {requests[p["request_id"]]["kind"] for p in assistant["proposals"] if "確認待ち" in p["decisions"].values()}
    evaluations = [p for p in assistant["proposals"] if requests[p["request_id"]]["kind"] == "evaluation" and requests[p["request_id"]]["story_version_id"] == version_id]
    evaluation_stale=False
    if evaluations:
        base=requests[evaluations[-1]["request_id"]]
        previous=base["context"]["artwork"]
        evaluation_stale=previous["style"] != artwork["style"] or active_characters(previous) != active_characters(artwork) or any(
            (previous["panels"].get(panel_key(t["page"],t["panel"])) or {}).get("direction") != (artwork["panels"].get(panel_key(t["page"],t["panel"])) or {}).get("direction") for t in base["targets"])
    references_missing=False
    if root:
        references_missing=any(c["primary_reference_id"] and not (Path(root)/next(i["path"] for i in c["references"] if i["id"]==c["primary_reference_id"])).is_file() for c in characters)
    expected = [version_id, artwork["revision"], composition["revision"], asset_token]
    rendered = sum(studio["renders"].get(str(p["page"]),{}).get("fingerprint") == expected for p in story["pages"]) if story else 0
    current_exports = [e for e in studio["exports"] if e["fingerprint"] == expected]
    exported = {p for e in current_exports for p in e["pages"]}
    total = len(panels)
    def stage(label, state, menu, section, detail="", optional=False):
        return dict(label=label, state=state, menu=menu, section=section, detail=detail, optional=optional)
    rows = [stage("プロジェクト作成", "完了", "プロジェクト管理", None, project.title),
            stage("ストーリー生成・JSON取り込み", "完了" if story else "未着手", "ストーリー制作", "生成・取り込み"),
            stage("キャラクター設定生成・採用", "進行中" if "characters" in pending else "完了" if characters else "未着手", "キャラクター設定", "AI一括生成・採用", f"登録 {len(characters)}人"),
            stage("作風設定・基準画像登録", "要確認" if references_missing else "完了" if characters and all(c["primary_reference_id"] for c in characters) else "進行中" if characters else "未着手", "画像制作・画像管理", "基準画像・作風", "保存済み作風："+artwork["style"]),
            stage("作画指示の一括生成・採用", "要確認" if any(p["direction_stale"] for p in panels) else "完了" if total and all(p["direction"] for p in panels) else "進行中" if any(p["direction"] for p in panels) or "directions" in pending else "未着手", "作画指示・AI評価改善", "AI一括生成・採用", f"設定 {sum(p['direction'] for p in panels)}/{total}コマ"),
            stage("作画評価・改善（任意）", "要確認" if evaluation_stale else "完了" if evaluations else "未着手", "作画指示・AI評価改善", "AI評価", "スキップ中" if studio["skip_evaluation"] else "必要に応じて実施", True),
            stage("コマ画像生成・登録・採用", "要確認" if any(p["image_stale"] for p in panels) else "完了" if total and all(p["selected"] for p in panels) else "進行中" if any(p["status"] in ("候補あり","採用済み") for p in panels) else "未着手", "画像制作・画像管理", "コマ画像・採用一覧", f"採用 {sum(p['selected'] for p in panels)}/{total}コマ"),
            stage("漫画ページ合成", "完了" if story and rendered == len(story["pages"]) else "要確認" if studio["renders"] and not rendered else "進行中" if rendered or composition["pages"] else "未着手", "漫画ページ編集", None, f"現在の条件で描画済み {rendered}/{len(story['pages']) if story else 0}ページ"),
            stage("PNG・PDF出力", "完了" if story and set(p["page"] for p in story["pages"]) <= exported else "要確認" if studio["exports"] and not current_exports else "進行中" if current_exports else "未着手", "PNG・PDF書き出し", None, "生成記録です。ダウンロードしたファイルの保存も確認してください。")]
    next_step = next((r for r in rows if r["state"] != "完了" and not r["optional"]), None)
    return rows, next_step, panels
