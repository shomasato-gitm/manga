"""ChatGPTとの手動受け渡し用JSON。通信やAPI呼び出しは行わない。"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MAX_IMPORT_BYTES = 1024 * 1024


class StoryValidationError(ValueError):
    """入力箇所が分かる、利用者向けの検証エラー。"""


@dataclass
class StoryImport:
    story: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StoryValidationError(f"項目「{key}」が重複しています。1つにまとめてください。")
        result[key] = value
    return result


def _invalid_constant(value):
    raise StoryValidationError(f"{value}はJSONの値として使えません。")


def decode_story_file(content: bytes) -> str:
    if len(content) > MAX_IMPORT_BYTES:
        raise StoryValidationError("JSONファイルは1MB以下にしてください。")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise StoryValidationError("ファイルをUTF-8形式で保存し直してください。") from None


def parse_story(text: str, expected_pages: Optional[int] = None, default_title: str = "") -> StoryImport:
    """BOM・Markdownのコードフェンスと、フェンス前後の説明文を許可する。"""
    if not isinstance(text, str) or not text.strip():
        raise StoryValidationError("ChatGPTが出力したJSONを貼り付けてください。")
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise StoryValidationError("JSONは1MB以下にしてください。")
    text = text.lstrip("\ufeff").strip()
    # 有効なJSONのセリフにバッククォートが含まれていても壊さない。
    fences = [] if text.startswith(("{", "[")) else re.findall(
        r"```(?:json)?\s*\n?(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
    )
    if len(fences) > 1:
        raise StoryValidationError("コードブロックが複数あります。ストーリーのJSONを1つだけ貼り付けてください。")
    if fences:
        text = fences[0].strip()
    try:
        data = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    except json.JSONDecodeError as error:
        raise StoryValidationError(
            f"JSONの{error.lineno}行{error.colno}列付近を確認してください。"
            "キーと文字列には半角のダブルクォートを使い、末尾の余分なカンマやコメントを取り除いてください。"
        ) from None
    except RecursionError:
        raise StoryValidationError("JSONの入れ子が深すぎます。指定の形式に直してください。") from None
    except StoryValidationError:
        raise
    except ValueError:
        raise StoryValidationError("JSONに大きすぎる数値があります。ページ番号・コマ番号は小さな整数にしてください。") from None
    return normalize_story(data, expected_pages, default_title)


def normalize_story(data: Any, expected_pages: Optional[int] = None, default_title: str = "") -> StoryImport:
    warnings = []

    def pick(obj, names, default=None):
        found = [name for name in names if name in obj]
        if len(found) > 1 and any(obj[name] != obj[found[0]] for name in found[1:]):
            raise StoryValidationError(f"同じ意味の項目「{' / '.join(found)}」の値が異なります。1つにまとめてください。")
        return obj[found[0]] if found else default

    def object_value(value, location):
        if not isinstance(value, dict):
            raise StoryValidationError(f"{location}はJSONオブジェクト（{{...}}）にしてください。")
        return value

    def text_value(value, location, maximum=4000, required=False):
        if value is None and not required:
            return ""
        if not isinstance(value, str):
            raise StoryValidationError(f"{location}は文字列にしてください。")
        value = value.strip()
        if required and not value:
            raise StoryValidationError(f"{location}を入力してください。")
        if len(value) > maximum:
            raise StoryValidationError(f"{location}は{maximum}文字以内にしてください。")
        return value

    def number(value, expected, location):
        if isinstance(value, str) and len(value.strip()) <= 3 and value.strip().isdecimal():
            value = int(value.strip())
        if type(value) is not int or value != expected:
            raise StoryValidationError(f"{location}は重複・欠番のない1からの連番にしてください（ここは{expected}）。")
        return value

    def unknown(obj, known, location):
        extra = set(obj) - set(known)
        if extra:
            warnings.append(f"{location}の未対応項目を取り込みませんでした：{'、'.join(sorted(extra))}")

    def dialogue(value, location):
        if isinstance(value, list):
            lines = []
            for item in value:
                if isinstance(item, dict):
                    speaker = text_value(pick(item, ("speaker", "character", "話者"), ""), f"{location}の話者", 100)
                    line = text_value(pick(item, ("text", "セリフ"), ""), location, required=True)
                    unknown(item, ("speaker", "character", "話者", "text", "セリフ"), location)
                    lines.append(f"{speaker}：{line}" if speaker else line)
                else:
                    lines.append(text_value(item, location))
            value = "\n".join(lines)
        return text_value(value, location)

    object_value(data, "ストーリー全体")
    wrapped = pick(data, ("story", "ストーリー"))
    if wrapped is not None:
        unknown(data, ("story", "ストーリー"), "外側のオブジェクト")
        data = object_value(wrapped, "story")
    schema_version = data.get("schema_version", 1)
    if type(schema_version) is not int or schema_version != 1:
        raise StoryValidationError("ストーリーのschema_versionは1にしてください。")
    title = pick(data, ("title", "タイトル"), default_title)
    title = text_value(title, "ストーリーのタイトル", 100, required=True)
    summary = text_value(pick(data, ("summary", "あらすじ", "概要"), ""), "全体のあらすじ")
    pages = pick(data, ("pages", "ページ", "ページ一覧"))
    if not isinstance(pages, list) or not 1 <= len(pages) <= 100:
        raise StoryValidationError("pages（ページ）は1〜100件の配列にしてください。")
    if expected_pages is not None and len(pages) != expected_pages:
        raise StoryValidationError(f"ページ数が設定と一致しません。{expected_pages}ページ必要ですが、{len(pages)}ページあります。")
    normalized_pages = []
    for page_index, page in enumerate(pages, 1):
        location = f"{page_index}ページ目"
        object_value(page, location)
        number(pick(page, ("page", "page_number", "ページ番号", "ページ"), page_index), page_index, f"{location}のページ番号")
        panels = pick(page, ("panels", "コマ", "コマ一覧"))
        if not isinstance(panels, list) or not 1 <= len(panels) <= 6:
            raise StoryValidationError(f"{location}のpanels（コマ）は1〜6件の配列にしてください。目安は3コマです。")
        if len(panels) != 3:
            warnings.append(f"{location}は{len(panels)}コマです（目安は3コマ）。")
        normalized_panels = []
        for panel_index, panel in enumerate(panels, 1):
            panel_location = f"{location}・{panel_index}コマ目"
            object_value(panel, panel_location)
            number(pick(panel, ("panel", "panel_number", "コマ番号"), panel_index), panel_index, f"{panel_location}のコマ番号")
            normalized_panels.append({
                "panel": panel_index,
                "description": text_value(pick(panel, ("description", "scene", "描写", "場面")), f"{panel_location}の描写", required=True),
                "dialogue": dialogue(pick(panel, ("dialogue", "dialogues", "セリフ"), ""), f"{panel_location}のセリフ"),
                "narration": text_value(pick(panel, ("narration", "ナレーション"), ""), f"{panel_location}のナレーション", 2000),
            })
            unknown(panel, ("panel", "panel_number", "コマ番号", "description", "scene", "描写", "場面", "dialogue", "dialogues", "セリフ", "narration", "ナレーション"), panel_location)
        normalized_pages.append({
            "page": page_index,
            "summary": text_value(pick(page, ("summary", "概要", "要約"), ""), f"{location}の概要", 2000),
            "panels": normalized_panels,
        })
        unknown(page, ("page", "page_number", "ページ番号", "ページ", "summary", "概要", "要約", "panels", "コマ", "コマ一覧"), location)
    unknown(data, ("schema_version", "title", "タイトル", "summary", "あらすじ", "概要", "pages", "ページ", "ページ一覧"), "ストーリー全体")
    return StoryImport({"schema_version": 1, "title": title, "summary": summary, "pages": normalized_pages}, warnings)
