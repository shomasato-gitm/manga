"""キャラクターと作画指示の項目・検証。画像データは別ファイルで保存する。"""

from manga.models import validate_id


CHARACTER_FIELDS = {
    "name": "名前", "age": "年齢", "gender": "性別", "hairstyle": "髪型",
    "hair_color": "髪色", "eye_color": "目の色", "body_type": "体格",
    "clothing": "服装", "personality": "性格", "features": "特徴",
}
CHARACTER_EXTRA_FIELDS = {"height": "身長", "role": "物語上の役割", "visual_identity": "視覚的な差別化"}
CHARACTER_ALL_FIELDS = {**CHARACTER_FIELDS, **CHARACTER_EXTRA_FIELDS}
DIRECTION_FIELDS = {
    "location": "場所", "time_of_day": "時間帯", "expression": "表情",
    "pose": "ポーズ", "camera_angle": "カメラ角度", "composition": "構図",
    "background": "背景", "lighting": "光", "color_palette": "色彩",
    "dramatic_role": "演出的な役割", "continuity": "前後のコマとの連続性",
}
DEFAULT_STYLE = "白黒の日本漫画。読みやすい線画と整理された背景。"


def text(value, label, maximum=2000, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{label}は文字列にしてください。")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label}を入力してください。")
    if len(value) > maximum:
        raise ValueError(f"{label}は{maximum}文字以内にしてください。")
    return value


def character_fields(values):
    fields = {
        key: text(values.get(key, ""), label, 100 if key == "name" else 2000, required=key == "name")
        for key, label in CHARACTER_FIELDS.items()
    }
    # 旧JSONに追加項目がなくても、そのまま読み込める。
    fields.update({key: text(values[key], label) for key, label in CHARACTER_EXTRA_FIELDS.items() if key in values})
    return fields


def direction_fields(values):
    fields = {key: text(values.get(key, ""), label) for key, label in DIRECTION_FIELDS.items()}
    ids = values.get("character_ids", [])
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("登場キャラクターの指定が不正です。")
    fields["character_ids"] = [validate_id(value) for value in ids]
    return fields


def panel_key(page, panel):
    if type(page) is not int or not 1 <= page <= 100 or type(panel) is not int or not 1 <= panel <= 6:
        raise ValueError("ページ番号・コマ番号が不正です。")
    return f"{page}:{panel}"


def image_context_changed(image, entry, artwork):
    """脚本が同じでも、採用後の作画・人物変更で画像が古くなりうる。"""
    context = image["context"]
    if context["style"] != artwork["style"] or direction_fields(context["direction"] or {}) != direction_fields(entry["direction"] or {}):
        return True
    current = {character["id"]: character for character in artwork["characters"] if not character["deleted_at"]}
    return any(character["id"] not in current or character_fields(character) != character_fields(current[character["id"]])
               for character in context["characters"])


def empty_artwork(project_id):
    return {
        "schema_version": 1, "project_id": validate_id(project_id), "revision": 0,
        "style": DEFAULT_STYLE, "characters": [], "panels": {},
    }


def image_metadata_valid(image, category):
    validate_id(image["id"])
    if image["path"] != f"images/{category}/{image['id']}.png":
        raise ValueError("画像の保存先が不正です。")
    text(image["original_name"], "画像ファイル名", 200, required=True)
    if (type(image["width"]) is not int or type(image["height"]) is not int
            or image["width"] < 1 or image["height"] < 1 or image["width"] * image["height"] > 24_000_000):
        raise ValueError("画像サイズが不正です。")
    text(image["saved_at"], "画像の保存日時", required=True)


def source_panel_valid(panel, number):
    if not isinstance(panel, dict) or type(panel["panel"]) is not int or panel["panel"] != number:
        raise ValueError("元のコマが不正です。")
    text(panel["description"], "元のコマの描写", 4000, required=True)
    text(panel["dialogue"], "元のコマのセリフ", 4000)
    text(panel["narration"], "元のコマのナレーション", 2000)


def validate_artwork(data, project_id):
    """壊れた設定や、保存先を外れる画像パスの上書きを防ぐ。"""
    if not isinstance(data, dict) or type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["project_id"] != project_id:
        raise ValueError("作画設定の形式が不正です。")
    if type(data["revision"]) is not int or data["revision"] < 0:
        raise ValueError("作画設定の版番号が不正です。")
    text(data["style"], "作風", required=True)
    if not isinstance(data["characters"], list) or not isinstance(data["panels"], dict):
        raise ValueError("作画設定の一覧が不正です。")
    character_ids, image_ids = set(), set()
    for character in data["characters"]:
        validate_id(character["id"])
        if character["id"] in character_ids or character_fields(character) != {key: character[key] for key in character_fields(character)}:
            raise ValueError("キャラクター設定が不正です。")
        character_ids.add(character["id"])
        text(character["created_at"], "キャラクターの作成日時", required=True)
        text(character["updated_at"], "キャラクターの更新日時", required=True)
        if character["deleted_at"] is not None:
            text(character["deleted_at"], "削除日時", required=True)
        if not isinstance(character["references"], list):
            raise ValueError("参照画像の一覧が不正です。")
        reference_ids = set()
        for image in character["references"]:
            image_metadata_valid(image, "references")
            text(image["view"], "参照画像の種類", 100, required=True)
            text(image["note"], "参照画像のメモ", 500)
            if image["id"] in image_ids:
                raise ValueError("画像IDが重複しています。")
            image_ids.add(image["id"])
            reference_ids.add(image["id"])
        if ((reference_ids and character["primary_reference_id"] is None)
                or (character["primary_reference_id"] is not None and character["primary_reference_id"] not in reference_ids)):
            raise ValueError("基準画像の指定が不正です。")
    for key, entry in data["panels"].items():
        if key != panel_key(entry["page"], entry["panel"]):
            raise ValueError("コマの保存先が不正です。")
        direction = entry["direction"]
        if direction is not None:
            fields = direction_fields(direction)
            if any(value not in character_ids for value in fields["character_ids"]):
                raise ValueError("登場キャラクターが見つかりません。")
            validate_id(direction["story_version_id"])
            source_panel_valid(direction["source_panel"], entry["panel"])
        if not isinstance(entry["candidates"], list) or not isinstance(entry["selection_history"], list):
            raise ValueError("候補画像の一覧が不正です。")
        candidate_ids = set()
        for image in entry["candidates"]:
            image_metadata_valid(image, "panels")
            validate_id(image["story_version_id"])
            source_panel_valid(image["source_panel"], entry["panel"])
            context = image["context"]
            if not isinstance(context, dict) or not isinstance(context["characters"], list):
                raise ValueError("候補画像の記録が不正です。")
            text(context["style"], "登録時の作風", required=True)
            if context["direction"] is not None:
                direction_fields(context["direction"])
            for character in context["characters"]:
                character_fields(character)
            if image["id"] in image_ids:
                raise ValueError("画像IDが重複しています。")
            image_ids.add(image["id"])
            candidate_ids.add(image["id"])
        if entry["selected_image_id"] is not None and entry["selected_image_id"] not in candidate_ids:
            raise ValueError("採用画像の指定が不正です。")
        if any(selection["image_id"] not in candidate_ids for selection in entry["selection_history"]):
            raise ValueError("採用履歴の指定が不正です。")
    return data
