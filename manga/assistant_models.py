"""AI制作提案の厳密な入力境界。確定データはここでは変更しない。"""

from manga.artwork_models import CHARACTER_ALL_FIELDS, DIRECTION_FIELDS, character_fields, direction_fields, panel_key
from manga.editorial_models import obj, reference_fields, schema, text
from manga.models import validate_id


KINDS = {"characters": "キャラクター一括生成", "directions": "作画指示一括生成", "references": "参考作品候補", "evaluation": "作画評価", "improvement": "作画改善"}
DRAWING_CRITERIA = {
    "alignment": "ストーリーとの整合性", "composition": "構図の魅力", "emotion": "感情表現",
    "naturalness": "表情・ポーズの自然さ", "camera": "カメラワーク", "continuity": "コマの連続性",
    "consistency": "キャラクターの一貫性", "readability": "漫画としての読みやすさ",
}
PROVENANCE = ("ストーリー明記", "AI提案", "要確認", "ユーザー編集")
REFERENCE_AI_FIELDS = {"staging": "演出方法", "application": "自作品への応用", "confirmation_status": "確認状態", "basis": "確認の根拠"}


def array(value, label, maximum=600, empty=False):
    if not isinstance(value, list) or len(value) > maximum or (not empty and not value):
        raise ValueError(f"{label}は{'0' if empty else '1'}〜{maximum}件の配列にしてください。")
    return value


def required_fields(values, labels):
    obj(values, "設定項目")
    for key, label in labels.items():
        if key not in values:
            raise ValueError(f"{label}（{key}）がありません。不明なら空文字にし、questionsに確認事項を記載してください。")


def questions(values):
    return [text(value, "確認事項", 1000, True) for value in array(values, "questions", 30, True)]


def targets_for(story, page=None, panel=None):
    all_targets = [{"page": p["page"], "panel": cell["panel"]} for p in story["pages"] for cell in p["panels"]]
    if page is not None and type(page) is not int or panel is not None and type(panel) is not int:
        raise ValueError("対象ページ・コマは整数にしてください。")
    result = [item for item in all_targets if (page is None or item["page"] == page) and (panel is None or item["panel"] == panel)]
    if not result or panel is not None and page is None:
        raise ValueError("対象のページ・コマがありません。")
    return result


def validate_targets(values, story, allowed=None):
    result, seen = [], set()
    possible = {panel_key(t["page"], t["panel"]) for t in targets_for(story)}
    if allowed is not None:
        possible &= {panel_key(t["page"], t["panel"]) for t in allowed}
    for target in array(values, "対象コマ"):
        obj(target, "対象コマ")
        key = panel_key(target.get("page"), target.get("panel"))
        if key not in possible or key in seen:
            raise ValueError("対象ページ・コマが範囲外か重複しています。")
        seen.add(key)
        result.append(dict(page=target["page"], panel=target["panel"]))
    return result


def normalize_item(kind, item, request):
    obj(item, "提案")
    item_id = text(item.get("id"), "提案ID", 100, True)
    checked = dict(id=item_id, questions=questions(item.get("questions", [])))
    if kind == "characters":
        values = obj(item.get("fields"), "キャラクター設定")
        # Phase 10-Aの関係設定は任意追加。保存済みの旧提案には要求しない。
        required_fields(values, {k:v for k,v in CHARACTER_ALL_FIELDS.items() if k != 'relationships'})
        fields = character_fields(values)
        existing_id = item.get("existing_id")
        if existing_id is not None:
            validate_id(existing_id)
            if existing_id not in {c["id"] for c in request["context"]["artwork"]["characters"] if not c["deleted_at"]}:
                raise ValueError("既存人物IDが対象データにありません。新規人物はexisting_idをnullにしてください。")
        provenance = obj(item.get("provenance"), "事実と提案の区別")
        normalized = {}
        for key, value in fields.items():
            if not value and key not in provenance:
                continue
            source = obj(provenance.get(key), f"{key}の根拠")
            if source.get("status") not in PROVENANCE:
                raise ValueError(f"{key}の確認状態が不正です。")
            normalized[key] = dict(status=source["status"], evidence=text(source.get("evidence", ""), "根拠", 2000, source["status"] == "ストーリー明記"))
        checked.update(fields=fields, existing_id=existing_id, provenance=normalized)
    elif kind in ("directions", "improvement"):
        values = obj(item.get("fields"), "作画指示")
        required_fields(values, {**DIRECTION_FIELDS, "character_ids": "登場人物ID"})
        fields = direction_fields(values)
        known = {c["id"] for c in request["context"]["artwork"]["characters"] if not c["deleted_at"]}
        if not set(fields["character_ids"]) <= known:
            raise ValueError("登場人物IDが存在しないか削除済みです。人物名をIDの代わりに使わないでください。")
        target = validate_targets([item], request["context"]["story"], request["targets"])[0]
        checked.update(**target, fields=fields, reason=text(item.get("reason"), "演出・改善理由", 4000, True))
    elif kind == "references":
        values = obj(item.get("fields"), "参考作品")
        required_fields(values, {k: k for k in ("title", "medium", "reason", "techniques")})
        checked["fields"] = reference_fields(values)
        for key, label in REFERENCE_AI_FIELDS.items():
            checked[key] = text(item.get(key), label, 2000, True)
        reference_registration_fields(checked)
        if checked["confirmation_status"] not in ("要確認", "ユーザー確認済み"):
            raise ValueError("作品の確認状態は要確認またはユーザー確認済みにしてください。")
    else:
        raise ValueError("提案の種類が不正です。")
    return checked


def reference_registration_fields(item):
    values = dict(item["fields"])
    values["scene_summary"] = item["staging"]
    values["notes"] = "\n".join(filter(None, [values["notes"], f"自作への応用：{item['application']}", f"確認状態：{item['confirmation_status']}", f"根拠：{item['basis']}"]))
    return reference_fields(values)


def normalize_payload(data, request):
    obj(data, "制作アシスタントJSON")
    schema(data)
    for key in ("request_id", "project_id", "story_version_id", "kind"):
        if data.get(key) != request[key if key != "request_id" else "id"]:
            raise ValueError(f"{key}が生成依頼と一致しません。対応するプロンプトから生成してください。")
    envelope = dict(schema_version=1, request_id=request["id"], project_id=request["project_id"], story_version_id=request["story_version_id"], kind=request["kind"])
    if request["kind"] == "evaluation":
        scores = obj(data.get("scores"), "作画評価")
        result = {}
        for key, label in DRAWING_CRITERIA.items():
            entry = obj(scores.get(key), label)
            score = entry.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
                raise ValueError(f"{label}は0〜10点にしてください。")
            affected = array(entry.get("targets"), f"{label}の対象コマ", empty=True)
            result[key] = dict(score=score, **{name: text(entry.get(name), f"{label}の{name}", 4000, name == "reason") for name in ("good", "problem", "reason", "suggestion")},
                               targets=validate_targets(affected, request["context"]["story"], request["targets"]) if affected else [])
        envelope.update(scores=result, summary=text(data.get("summary"), "評価概要", 4000, True))
    else:
        items = [normalize_item(request["kind"], item, request) for item in array(data.get("items"), "提案一覧", 600 if request["kind"] in ("directions", "improvement") else 50)]
        ids = [item["id"] for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("提案IDが重複しています。")
        if request["kind"] in ("directions", "improvement"):
            validate_targets(items, request["context"]["story"], request["targets"])
            if len(items) != len(request["targets"]):
                raise ValueError("指定した対象コマの一部が欠けています。ページ単位で新しい依頼を作成することもできます。")
        existing = [item["existing_id"] for item in items if item.get("existing_id")]
        if len(existing) != len(set(existing)):
            raise ValueError("同じ既存人物IDへの提案が重複しています。")
        envelope["items"] = items
    return envelope
