"""ChatGPTの画像生成画面へ手動で渡す日本語プロンプト。通信処理は持たない。"""

import json
from pathlib import Path

from manga.artwork_models import CHARACTER_FIELDS, DIRECTION_FIELDS, panel_key


NO_TEXT = "画像にはセリフ、吹き出し、ナレーション、文字、擬音、ロゴ、透かしを描き込まないでください。文字は後工程で配置します。"
CONSISTENCY = (
    "登録設定と添付した基準画像を使い、同じ人物の髪型・髪色・目の色・体格・服装・特徴を統一してください。"
    "顔立ちや衣装を勝手に変更せず、別の人物と特徴を混ぜないでください。"
    "設定と参照画像に矛盾がある場合は推測で変更せず、確認を求めてください。"
)


def _character_description(character):
    lines = [f"{label}：{character[key] or '未指定'}" for key, label in CHARACTER_FIELDS.items()]
    references = character["references"]
    if references:
        lines.append("参照画像（ユーザーがこの会話に手動で添付するファイル）：")
        for image in references:
            primary = "・基準画像" if image["id"] == character["primary_reference_id"] else ""
            lines.append(f"- {Path(image['path']).name}：{image['view']}{primary}。{image['note']}")
    else:
        lines.append("参照画像は未登録です。まずこの設定に忠実に描いてください。")
    return "\n".join(lines)


def character_image_prompt(character, style, view, expression):
    return (
        "漫画のキャラクター基準画像を1枚作成してください。\n"
        f"作風：{style}\n向き・種類：{view}\n表情：{expression or '自然な表情'}\n\n"
        + _character_description(character)
        + f"\n\n{CONSISTENCY}\n背景は人物の外見が分かりやすいシンプルなものにしてください。"
        "全身指定の場合は頭から足先まで収めてください。表情差分では外見と服装を変えず、指定の表情を優先してください。\n"
        + NO_TEXT
    )


def panel_image_prompt(project, story, page, panel, artwork):
    entry = artwork["panels"].get(panel_key(page["page"], panel["panel"]))
    direction = entry["direction"] if entry else None
    if direction is None:
        raise ValueError("先にこのコマの作画指示を保存してください。")
    if direction["source_panel"] != panel:
        raise ValueError("このコマのストーリーが変更されています。作画指示を確認し、保存し直してください。")
    characters = []
    for character_id in direction["character_ids"]:
        character = next((item for item in artwork["characters"] if item["id"] == character_id), None)
        if character is None or character["deleted_at"]:
            raise ValueError("削除済みのキャラクターが指定されています。登場キャラクターを選び直して保存してください。")
        characters.append(_character_description(character))
    context = {
        "漫画のタイトル": project.title, "ジャンル": project.genre,
        "物語全体": story["summary"], "ページの概要": page["summary"],
        "コマの描写": panel["description"], "セリフ（内容理解用）": panel["dialogue"],
        "ナレーション（内容理解用）": panel["narration"],
    }
    directions = "\n".join(f"{label}：{direction[key] or '描写に合わせる'}" for key, label in DIRECTION_FIELDS.items())
    people = "\n\n".join(characters) if characters else "登場人物なし。背景や物を中心に描き、人物を追加しないでください。"
    return (
        f"漫画の{page['page']}ページ・{panel['panel']}コマ目のイラストを1枚作成してください。ページ全体の合成は不要です。\n"
        f"作風：{artwork['style']}\n\n物語の文脈：\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"作画指示：\n{directions}\n\n登場キャラクター：\n{people}\n\n{CONSISTENCY}\n"
        "セリフとナレーションは状況と感情を理解するためだけの情報です。\n" + NO_TEXT
    )
