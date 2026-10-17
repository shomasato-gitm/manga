"""右から左に読むコマ配置と、互換性を保つページ編集データ。座標は0〜1。"""

import math
from uuid import UUID, uuid5

from manga.models import validate_id
from manga.story import normalize_story


TEMPLATES = {"hero_top": "上段を大きく・下段は右→左", "rows": "縦積み", "grid": "2列・右→左", "hero_right": "右側を大きく・左側は上→下"}
ASPECTS = {"portrait": ("縦長 2:3", 1.5), "a4": ("A判の比率", math.sqrt(2)), "square": ("正方形", 1.0)}
WIDTHS = (600, 1080, 1600, 2000)
DEFAULT_SETTINGS = dict(width=1080, aspect="portrait", margin=4.0, gutter=2.0, border=0.3, font_path="", dpi=150)


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}は{low}〜{high}で指定してください。")
    return value


def validate_settings(settings):
    if type(settings["width"]) is not int or settings["width"] not in WIDTHS or settings["aspect"] not in ASPECTS:
        raise ValueError("出力サイズ・縦横比が不正です。")
    for key, low, high in (("margin", 1, 10), ("gutter", 0, 5), ("border", 0, 1)):
        number(settings[key], key, low, high)
    if not isinstance(settings["font_path"], str) or len(settings["font_path"]) > 2000:
        raise ValueError("フォントのパスが不正です。")
    if type(settings["dpi"]) is not int or settings["dpi"] not in (72, 150, 300):
        raise ValueError("DPIは72・150・300から選択してください。")
    return settings


def dimensions(settings, width=None):
    width = width or settings["width"]
    return width, round(width * ASPECTS[settings["aspect"]][1])


def rect(x, y, width, height):
    return dict(x=x, y=y, width=width, height=height)


def validate_rect(value, label, minimum=0.08):
    for key in ("x", "y"):
        number(value[key], f"{label}の{key}", 0, 1)
    for key in ("width", "height"):
        number(value[key], f"{label}の{key}", minimum, 1)
    if value["x"] + value["width"] > 1.000001 or value["y"] + value["height"] > 1.000001:
        raise ValueError(f"{label}が枠の外へ出ています。位置とサイズを調整してください。")
    return value


def validate_frames(frames, count):
    if not isinstance(frames, list) or len(frames) != count:
        raise ValueError("配置するコマ数がストーリーと一致しません。")
    for index, frame in enumerate(frames):
        validate_rect(frame, f"{index + 1}コマ目")
        for other in frames[:index]:
            overlap_x = min(frame["x"] + frame["width"], other["x"] + other["width"]) - max(frame["x"], other["x"])
            overlap_y = min(frame["y"] + frame["height"], other["y"] + other["height"]) - max(frame["y"], other["y"])
            if overlap_x > 0.000001 and overlap_y > 0.000001:
                raise ValueError("コマが重なっています。位置・サイズを調整してください。")
    return frames


def template_frames(template, count, settings):
    if template not in TEMPLATES or type(count) is not int or not 1 <= count <= 6:
        raise ValueError("レイアウトテンプレート・コマ数が不正です。")
    width, height = dimensions(settings)
    margin = width * settings["margin"] / 100
    gx = width * settings["gutter"] / 100 / (width - margin * 2)
    gy = width * settings["gutter"] / 100 / (height - margin * 2)

    def rows(n):
        h = (1 - gy * (n - 1)) / n
        return [rect(0, index * (h + gy), 1, h) for index in range(n)]

    def grid(n, top=0, available=1):
        row_count = math.ceil(n / 2)
        h = (available - gy * (row_count - 1)) / row_count
        result = []
        for index in range(row_count):
            cells = min(2, n - 2 * index)
            w = (1 - gx * (cells - 1)) / cells
            for cell in range(cells):
                result.append(rect((cells - 1 - cell) * (w + gx), top + index * (h + gy), w, h))
        return result

    if count == 1 or template == "rows":
        frames = rows(count)
    elif template == "grid":
        frames = grid(count)
    elif template == "hero_top":
        frames = [rect(0, 0, 1, 0.52)] + grid(count - 1, 0.52 + gy, 0.48 - gy)
    else:
        left_width = 0.45 - gx / 2
        frames = [rect(0.45 + gx / 2, 0, 0.55 - gx / 2, 1)]
        for frame in rows(count - 1):
            frame["width"] = left_width
            frames.append(frame)
    return validate_frames(frames, count)


def default_bubble(project_id, page, panel, source, bubble_id=None):
    speech = source != "narration"
    return dict(
        id=bubble_id or str(uuid5(UUID(project_id), f"bubble:{page}:{panel}:{source}")), panel=panel,
        source=source, text=None if source != "custom" else "セリフを入力", visible=True,
        box=rect(0.50, 0.04, 0.46, 0.50) if speech else rect(0.03, 0.72, 0.94, 0.25),
        shape="ellipse" if speech else "rectangle", flow="vertical" if speech else "horizontal",
        tail=speech, font_scale=0.025,
    )


def default_page(project_id, page, version_id):
    return dict(template="hero_top", frames=None, source_page=page, source_version_id=version_id,
                bubbles=[default_bubble(project_id, page["page"], panel["panel"], source)
                         for panel in page["panels"] for source in ("dialogue", "narration")])


def validate_page(config):
    if config["template"] not in TEMPLATES:
        raise ValueError("レイアウトテンプレートが不正です。")
    validate_id(config["source_version_id"])
    source = config["source_page"]
    # ページ単体も、既存のストーリー検証と同じ規則で確認する。
    normalized = normalize_story(dict(title="検証", pages=[dict(source, page=1)])).story["pages"][0]
    if dict(normalized, page=source["page"]) != source or type(source["page"]) is not int or not 1 <= source["page"] <= 100:
        raise ValueError("配置の元になったページが不正です。")
    if config["frames"] is not None:
        validate_frames(config["frames"], len(source["panels"]))
    if not isinstance(config["bubbles"], list) or len(config["bubbles"]) > 60:
        raise ValueError("吹き出しは1ページ60個までです。")
    seen = set()
    for bubble in config["bubbles"]:
        validate_id(bubble["id"])
        if bubble["id"] in seen:
            raise ValueError("吹き出しIDが重複しています。")
        seen.add(bubble["id"])
        if type(bubble["panel"]) is not int or not 1 <= bubble["panel"] <= len(source["panels"]):
            raise ValueError("吹き出しの対象コマが不正です。")
        if bubble["source"] not in ("dialogue", "narration", "custom") or bubble["shape"] not in ("ellipse", "rectangle") or bubble["flow"] not in ("vertical", "horizontal"):
            raise ValueError("吹き出しの本文・形・文字方向が不正です。")
        if bubble["text"] is not None and (not isinstance(bubble["text"], str) or len(bubble["text"]) > 4000):
            raise ValueError("吹き出しの本文は4000文字以内にしてください。")
        if bubble["source"] == "custom" and bubble["text"] is None:
            raise ValueError("自由文の吹き出しには本文を指定してください。")
        if type(bubble["visible"]) is not bool or type(bubble["tail"]) is not bool:
            raise ValueError("吹き出しの表示設定が不正です。")
        validate_rect(bubble["box"], "吹き出し", 0.10)
        number(bubble["font_scale"], "文字サイズ", 0.012, 0.07)
    return config


def effective_page(saved, project_id, page, version_id):
    if saved and len(saved["source_page"]["panels"]) == len(page["panels"]):
        return saved
    return default_page(project_id, page, version_id)


def bubble_text(bubble, panel):
    return bubble["text"] if bubble["text"] is not None else panel.get(bubble["source"], "")
