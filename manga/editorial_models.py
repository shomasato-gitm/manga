"""参考分析・評価JSONの検証。ストーリーJSONの形式は変更しない。"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

from manga.models import validate_id
from manga.story import load_json_payload


REFERENCE_FIELDS = {
    "title": "作品名", "medium": "媒体", "genre": "参考作品のジャンル",
    "reason": "参考にしたい理由", "scene_summary": "好きなシーン・演出の概要",
    "techniques": "参考にしたいストーリー技法", "avoid": "参考にしたくない要素",
    "source_url": "出典URL", "notes": "メモ",
}
ANALYSIS_ASPECTS = {
    "opening": "読者を引き込む導入", "goal": "主人公の目的・欲求", "characters": "キャラクターの魅力",
    "emotion": "感情の変化", "foreshadowing": "伏線と回収", "surprise": "意外性やどんでん返し",
    "expression": "セリフ・沈黙・視覚的演出", "climax": "クライマックスの構成", "ending": "結末の余韻",
}
CRITERIA = {
    "opening": "導入の魅力", "characters": "キャラクターの魅力", "structure": "ストーリー構成",
    "emotion": "感情の盛り上がり", "dialogue": "セリフの自然さ", "foreshadowing": "伏線・回収",
    "ending": "結末の余韻", "originality": "オリジナリティ",
}
MODES = ("全体を改善する", "特定のページ・コマだけを改善する", "特定の評価項目だけを重点的に改善する")
SUBJECTIVE_NOTICE = "AIの採点は主観的な評価であり、作品の客観的な品質を保証するものではありません。"


def text(value, label, limit=4000, required=False):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{label}は空でない文字列にしてください。" if required else f"{label}は文字列にしてください。")
    if len(value.strip()) > limit:
        raise ValueError(f"{label}は{limit}文字以内にしてください。")
    return value.strip()


def obj(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}はJSONオブジェクトにしてください。")
    return value


def pick(data, key, label, default=None):
    if key in data and label in data and data[key] != data[label]:
        raise ValueError(f"{key}と{label}の値が異なります。1つにまとめてください。")
    return data.get(key, data.get(label, default))


def schema(data):
    if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
        raise ValueError("schema_versionは1にしてください。")


def reference_fields(values):
    result = {key: text(values.get(key, ""), label, 2000, key in ("title", "medium")) for key, label in REFERENCE_FIELDS.items()}
    if result["source_url"]:
        try:
            url = urlsplit(result["source_url"])
        except ValueError:
            raise ValueError("出典URLの形式が不正です。httpまたはhttpsのURLにしてください。") from None
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("出典URLは認証情報を含まないhttpまたはhttpsのURLにしてください。")
    return result


def normalize_analysis(data, reference_id):
    data = obj(data, "分析JSON")
    data = obj(pick(data, "analysis", "分析", data), "分析JSON")
    schema(data)
    if data.get("reference_id") != reference_id:
        raise ValueError("reference_idが選択中の参考作品と一致しません。専用プロンプトから作成してください。")
    reference_version = data.get("reference_version")
    if type(reference_version) is not int or reference_version < 1:
        raise ValueError("reference_versionはプロンプトに記載された参考作品の版番号にしてください。")
    aspects = obj(pick(data, "aspects", "分析項目"), "分析項目")
    normalized = {}
    for key, label in ANALYSIS_ASPECTS.items():
        item = obj(pick(aspects, key, label), label)
        status = pick(item, "status", "確認状態", "不明")
        if status not in ("ユーザー要約に基づく", "未確認", "不明"):
            raise ValueError(f"{label}の確認状態は「ユーザー要約に基づく」「未確認」「不明」のいずれかです。")
        normalized[key] = {
            "analysis": text(pick(item, "analysis", "分析"), f"{label}の分析", required=True),
            "abstract_technique": text(pick(item, "abstract_technique", "抽象的な技法", ""), f"{label}の抽象的な技法"),
            "basis": text(pick(item, "basis", "根拠", "不明"), f"{label}の根拠", required=True),
            "status": status,
        }
    return {"schema_version": 1, "reference_id": reference_id, "reference_version": reference_version, "aspects": normalized,
            "unknowns": text(pick(data, "unknowns", "不明点", ""), "不明点")}


def normalize_targets(values, story, required=False):
    if not isinstance(values, list) or len(values) > 600 or (required and not values):
        raise ValueError("対象ページ・コマは配列で指定してください。部分改善では1件以上必要です。")
    result = []
    for target in values:
        target = obj(target, "対象ページ・コマ")
        page = pick(target, "page", "ページ")
        panel = pick(target, "panel", "コマ")
        if isinstance(page, str) and page.isdecimal() and len(page) <= 3:
            page = int(page)
        if isinstance(panel, str) and panel.isdecimal() and len(panel) <= 2:
            panel = int(panel)
        if type(page) is not int or not 1 <= page <= len(story["pages"]):
            raise ValueError("評価・改善の対象ページがストーリーにありません。")
        if panel is not None and (type(panel) is not int or not 1 <= panel <= len(story["pages"][page - 1]["panels"])):
            raise ValueError(f"{page}ページの対象コマがストーリーにありません。")
        normalized = {"page": page, "panel": panel}
        if normalized not in result:
            result.append(normalized)
    return result


def score(value, label):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or not 0 <= number <= 10:
            raise ValueError
        return float(number)
    except (ValueError, InvalidOperation):
        raise ValueError(f"{label}の点数は0〜10の有限の数値にしてください。") from None


def normalize_evaluation(data, version_id, story):
    data = obj(data, "評価JSON")
    data = obj(pick(data, "evaluation", "評価", data), "評価JSON")
    schema(data)
    if data.get("story_version_id") != version_id:
        raise ValueError("story_version_idが評価対象のストーリー版と一致しません。対象版のプロンプトから再評価してください。")
    items = obj(pick(data, "criteria", "評価項目"), "評価項目")
    normalized = {}
    for key, label in CRITERIA.items():
        item = obj(pick(items, key, label), f"評価項目「{label}」")
        normalized[key] = {
            "score": score(pick(item, "score", "点数"), label),
            "good_points": text(pick(item, "good_points", "良い点"), f"{label}の良い点", required=True),
            "problems": text(pick(item, "problems", "問題点"), f"{label}の問題点", required=True),
            "reason": text(pick(item, "reason", "改善理由"), f"{label}の改善理由", required=True),
            "targets": normalize_targets(pick(item, "targets", "対象ページ・コマ", []), story),
            "suggestion": text(pick(item, "suggestion", "具体的な改善案"), f"{label}の具体的な改善案", required=True),
        }
    total = sum(Decimal(str(item["score"])) for item in normalized.values()) / Decimal(80) * 100
    return {"schema_version": 1, "story_version_id": version_id, "criteria": normalized,
            "total_score": float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "summary": text(pick(data, "summary", "総評", ""), "総評")}


def parse_analysis(raw, reference_id):
    return normalize_analysis(load_json_payload(raw), reference_id)


def parse_evaluation(raw, version_id, story):
    return normalize_evaluation(load_json_payload(raw), version_id, story)
