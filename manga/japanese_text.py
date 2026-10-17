"""日本語フォントの検証と、RAQMに依存しない横書き・縦書きの配置。"""

import os
import struct
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageDraw, ImageFont


class TextLayoutError(ValueError):
    pass


@dataclass(frozen=True)
class JapaneseFont:
    path: str
    index: int
    name: str
    signature: tuple
    codes: frozenset


PROBE = "日本語あいうアイウ、。「」ー"
VERTICAL_FORMS = dict(zip("、。「」『』（）［］｛｝…", "︑︒﹁﹂﹃﹄︵︶﹇﹈︷︸︙"))
ROTATE = set("ー－―〜～…()[]{}<>＝=：:；;")


@lru_cache(maxsize=16)
def _inspect_font(path, modified, size):
    try:
        with open(path, "rb") as file:
            header = file.read(12)
        count = struct.unpack(">I", header[8:12])[0] if header[:4] == b"ttcf" else 1
        if not 1 <= count <= 64:
            raise ValueError
        matches = []
        for index in range(count):
            with TTFont(path, fontNumber=index, lazy=True) as font:
                cmap = font.getBestCmap() or {}
                codes = frozenset(code for code, glyph in cmap.items() if glyph != ".notdef")
                if all(ord(char) in codes for char in PROBE):
                    name = font["name"].getDebugName(1) or Path(path).name
                    matches.append(JapaneseFont(path, index, name, (path, modified, size, index), codes))
        if matches:
            return next((font for font in matches if " JP" in font.name or "Hiragino" in font.name or "ヒラギノ" in font.name), matches[0])
    except (TTLibError, OSError, ValueError, struct.error, KeyError):
        pass
    raise TextLayoutError("日本語を描画できるフォントではありません。ヒラギノやNoto Sans CJK JP等のTTF・OTF・TTCを指定してください。")


def load_japanese_font(path):
    try:
        file = Path(path).expanduser().resolve()
        if file.suffix.lower() not in (".ttf", ".otf", ".ttc", ".otc"):
            raise ValueError
        stat = file.stat()
        return _inspect_font(str(file), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError) as error:
        if isinstance(error, TextLayoutError):
            raise
        raise TextLayoutError("フォントを読み込めません。日本語フォントのファイルパスを確認してください。") from error


@lru_cache(maxsize=1)
def _automatic_font():
    candidates = [
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"),
        Path("/System/Library/Fonts/ヒラギノ明朝 ProN.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
    ]
    for directory in (Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"):
        if directory.is_dir():
            for pattern in ("ヒラギノ*", "Hiragino*", "Noto*CJK*", "Noto*JP*", "Arial Unicode*"):
                candidates.extend(sorted(directory.glob(pattern)))
    for path in dict.fromkeys(candidates):
        if path.is_file():
            try:
                return load_japanese_font(str(path))
            except TextLayoutError:
                continue
    raise TextLayoutError("日本語フォントが見つかりません。日本語フォントをインストールし、出力設定またはMANGA_FONT_PATHでパスを指定してください。")


def find_japanese_font(path=""):
    configured = path.strip() or os.environ.get("MANGA_FONT_PATH", "").strip()
    return load_japanese_font(configured) if configured else _automatic_font()


def normalized_text(text):
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "  "))


def check_glyphs(text, spec):
    missing = sorted(set(char for char in normalized_text(text) if char != "\n" and ord(char) not in spec.codes))
    if missing:
        sample = "、".join(f"{char!r}(U+{ord(char):04X})" for char in missing[:8])
        raise TextLayoutError(f"選択した日本語フォントに未対応の文字があります：{sample}。別フォントか別の文字に変更してください。")


@lru_cache(maxsize=64)
def _pil_font(path, index, size, signature):
    return ImageFont.truetype(path, size=size, index=index, layout_engine=ImageFont.Layout.BASIC)


def _clusters(text):
    result = []
    for char in text:
        if unicodedata.combining(char) and result and result[-1] != "\n":
            result[-1] += char
        else:
            result.append(char)
    return result


def _vertical_char(char, spec):
    mapped = VERTICAL_FORMS.get(char)
    if mapped and ord(mapped) in spec.codes:
        return mapped, False
    return char, char in ROTATE or (char.isascii() and char.isalnum())


def _text_plan(text, font, spec, width, height, flow):
    if flow == "horizontal":
        lines, current = [], ""
        for cluster in _clusters(text):
            if cluster == "\n":
                lines.append(current)
                current = ""
                continue
            trial = current + cluster
            bounds = font.getbbox(trial)
            if bounds[2] - bounds[0] <= width:
                current = trial
            else:
                if not current:
                    return None
                lines.append(current)
                current = cluster
                box = font.getbbox(cluster)
                if box[2] - box[0] > width:
                    return None
        lines.append(current)
        line_height = max(round(font.size * 1.25), max((font.getbbox(line)[3] - font.getbbox(line)[1] for line in lines), default=0))
        return (lines, line_height) if line_height * len(lines) <= height else None
    clusters = _clusters(text)
    glyphs = [_vertical_char(char, spec)[0] for char in set(clusters) if char != "\n"]
    step = max(round(font.size * 1.2), max((max(font.getbbox(char)[2] - font.getbbox(char)[0], font.getbbox(char)[3] - font.getbbox(char)[1]) + 2 for char in glyphs), default=0))
    rows, columns = height // max(1, step), width // max(1, step)
    if not rows or not columns:
        return None
    content, current = [], []
    for cluster in clusters:
        if cluster == "\n":
            content.append(current)
            current = []
        else:
            if len(current) == rows:
                content.append(current)
                current = []
            current.append(cluster)
    content.append(current)
    return (content, step) if len(content) <= columns else None


def fit_text(text, width, height, flow, spec, requested_size, minimum_size):
    """収まる最大サイズを探し、最小でも収まらなければ明示エラー。省略はしない。"""
    text = normalized_text(text)
    check_glyphs(text, spec)
    low, high = max(1, minimum_size), max(1, requested_size)
    best = None
    while low <= high:
        size = (low + high) // 2
        font = _pil_font(spec.path, spec.index, size, spec.signature)
        plan = _text_plan(text, font, spec, width, height, flow)
        if plan:
            best = (font, plan)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise TextLayoutError("文字が吹き出しに収まりません。位置・サイズを広げるか、文章を短くしてください。文字は省略していません。")
    return text, best[0], best[1]


def draw_fitted_text(size, text, flow, spec, requested_size, minimum_size):
    width, height = size
    _, font, (content, step) = fit_text(text, width, height, flow, spec, requested_size, minimum_size)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if flow == "horizontal":
        for index, line in enumerate(content):
            bounds = font.getbbox(line)
            draw.text((-bounds[0], index * step - bounds[1]), line, font=font, fill="black")
    else:
        for column, chars in enumerate(content):
            for row, char in enumerate(chars):
                glyph, rotate = _vertical_char(char, spec)
                tile = Image.new("RGBA", (step, step), (0, 0, 0, 0))
                bounds = font.getbbox(glyph)
                ImageDraw.Draw(tile).text(((step - (bounds[2] - bounds[0])) / 2 - bounds[0],
                                          (step - (bounds[3] - bounds[1])) / 2 - bounds[1]), glyph, font=font, fill="black")
                if rotate:
                    tile = tile.rotate(-90)
                layer.alpha_composite(tile, (width - (column + 1) * step, row * step))
    return layer, font.size
