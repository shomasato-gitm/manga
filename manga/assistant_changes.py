"""確認画面と採用処理が共用する差分・競合確認・互換データ変換。"""

import copy
import json
import unicodedata
from uuid import uuid4

from manga.artwork_models import CHARACTER_ALL_FIELDS, character_fields, direction_fields, panel_key
from manga.assistant_models import normalize_item, reference_registration_fields
from manga.models import now_iso
from manga.storage import StorageError


def name_key(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def differences(before, after):
    before, after = before or {}, after or {}
    return [dict(field=key, before=before.get(key, ""), after=after.get(key, ""))
            for key in sorted(set(before) | set(after)) if before.get(key, "") != after.get(key, "")]


def display_differences(rows):
    """表では人物IDの配列も文字列にして、列の型を統一する。"""
    return [{key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
             for key, value in row.items()} for row in rows]


def active_characters(artwork):
    return [{"id": c["id"], **{key: c.get(key, "") for key in CHARACTER_ALL_FIELDS}} for c in artwork["characters"] if not c["deleted_at"]]


def context_unchanged(request, artwork, targets=None):
    base = request["context"]["artwork"]
    if active_characters(base) != active_characters(artwork) or base["style"] != artwork["style"]:
        raise StorageError("人物設定または作風が依頼後に更新されています。新しい依頼を作成してください。")
    for target in targets or request["targets"]:
        key = panel_key(target["page"], target["panel"])
        if (base["panels"].get(key) or {}).get("direction") != (artwork["panels"].get(key) or {}).get("direction"):
            raise StorageError(f"{key}の作画指示が依頼後に更新されています。新しい依頼を作成してください。")


def reference_values(item):
    return reference_registration_fields(item)


def item_before(kind, item, artwork, editorial):
    if kind == "characters":
        existing = next((c for c in artwork["characters"] if c["id"] == item.get("existing_id")), None)
        return {key: existing.get(key, "") for key in CHARACTER_ALL_FIELDS} if existing else {}
    if kind in ("directions", "improvement"):
        entry = artwork["panels"].get(panel_key(item["page"], item["panel"]))
        return direction_fields(entry["direction"]) if entry and entry["direction"] else {}
    return {}


def apply_items(kind, items, request, artwork, editorial, *, allow_duplicates=False):
    """メモリ上だけで候補を反映し、全件の検証が成功した結果だけを呼び出し元へ返す。"""
    artwork, editorial = copy.deepcopy(artwork), copy.deepcopy(editorial)
    changes = []
    existing_targets = set()
    for original in items:
        item = normalize_item(kind, original, request)
        before = item_before(kind, item, artwork, editorial)
        result_id = None
        if kind == "characters":
            fields, existing_id = character_fields(item["fields"]), item["existing_id"]
            duplicates = [c for c in artwork["characters"] if not c["deleted_at"] and c["id"] != existing_id and name_key(c["name"]) == name_key(fields["name"])]
            if duplicates and not allow_duplicates:
                raise ValueError("同名の既存人物または採用対象内の重複があります。既存人物を指定するか、別人として登録する確認をオンにしてください。")
            if existing_id:
                if existing_id in existing_targets:
                    raise ValueError("同じ人物への更新を複数選択しています。")
                existing_targets.add(existing_id)
                base = next(c for c in request["context"]["artwork"]["characters"] if c["id"] == existing_id)
                current = next((c for c in artwork["characters"] if c["id"] == existing_id and not c["deleted_at"]), None)
                if current is None or character_fields(current) != character_fields(base):
                    raise StorageError("既存人物が依頼後に更新・削除されました。新しい依頼を作成してください。")
                current.update(fields, updated_at=now_iso())
                result_id = existing_id
            else:
                result_id = str(uuid4())
                artwork["characters"].append(dict(fields, id=result_id, created_at=now_iso(), updated_at=now_iso(), deleted_at=None, references=[], primary_reference_id=None))
            after = fields
        elif kind in ("directions", "improvement"):
            context_unchanged(request, artwork, [item])
            key = panel_key(item["page"], item["panel"])
            source = request["context"]["story"]["pages"][item["page"] - 1]["panels"][item["panel"] - 1]
            entry = artwork["panels"].setdefault(key, dict(page=item["page"], panel=item["panel"], direction=None, candidates=[], selected_image_id=None, selection_history=[]))
            entry["direction"] = dict(item["fields"], story_version_id=request["story_version_id"], source_panel=copy.deepcopy(source), updated_at=now_iso())
            result_id, after = key, item["fields"]
        else:
            fields = reference_values(item)
            duplicates = [r for r in editorial["references"] if not r["deleted_at"] and name_key(r["title"]) == name_key(fields["title"]) and name_key(r["medium"]) == name_key(fields["medium"])]
            if duplicates and not allow_duplicates:
                raise ValueError("同じ作品名・媒体の参考作品があります。登録を見送るか、重複登録の確認をオンにしてください。")
            result_id, after = str(uuid4()), fields
            editorial["references"].append(dict(fields, id=result_id, version=1, created_at=now_iso(), updated_at=now_iso(), deleted_at=None))
        changes.append(dict(item_id=item["id"], result_id=result_id, before=before, after=after, differences=differences(before, after)))
    target = editorial if kind == "references" else artwork
    target["revision"] += 1
    return target, changes
