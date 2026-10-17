"""採用画像を読み取り専用で合成する。変更のないコマはPNGキャッシュを再利用する。"""

import hashlib
import json
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from manga.artwork_models import image_context_changed, panel_key
from manga.japanese_text import TextLayoutError, draw_fitted_text
from manga.page_layout import bubble_text, dimensions, effective_page, template_frames
from manga.storage import StorageError


@dataclass(frozen=True)
class RenderedPage:
    png: bytes
    width: int
    height: int
    errors: tuple
    warnings: tuple


class RenderCache:
    """元画像や無制限のPIL画像を保持しない、容量上限付きのコマキャッシュ。"""
    def __init__(self, max_bytes=32 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = self.hits = self.misses = 0

    def get(self, key):
        if key not in self.entries:
            self.misses += 1
            return None
        self.hits += 1
        self.entries.move_to_end(key)
        return self.entries[key]

    def put(self, key, value):
        size = len(value[0])
        if size > self.max_bytes:
            return
        if key in self.entries:
            self.bytes -= len(self.entries.pop(key)[0])
        while self.entries and self.bytes + size > self.max_bytes:
            self.bytes -= len(self.entries.popitem(last=False)[1][0])
        self.entries[key] = value
        self.bytes += size


def png_bytes(image, dpi=150):
    output = BytesIO()
    image.save(output, format="PNG", dpi=(dpi, dpi))
    return output.getvalue()


def pixel_frames(page, config, settings, width=None):
    width, height = dimensions(settings, width)
    margin = round(width * settings["margin"] / 100)
    available_w, available_h = width - margin * 2, height - margin * 2
    frames = config["frames"] or template_frames(config["template"], len(page["panels"]), settings)
    return [dict(x=margin + round(frame["x"] * available_w), y=margin + round(frame["y"] * available_h),
                 width=round(frame["width"] * available_w), height=round(frame["height"] * available_h)) for frame in frames]


def adopted_image(artwork, page, panel):
    entry = artwork["panels"].get(panel_key(page, panel))
    return next((image for image in entry["candidates"] if image["id"] == entry["selected_image_id"]), None) if entry else None


def _draw_bubble(image, bubble, content, page_width, border, font):
    width, height = image.size
    box = bubble["box"]
    inner_w, inner_h = width - border * 2, height - border * 2
    x, y = border + round(box["x"] * inner_w), border + round(box["y"] * inner_h)
    bw = max(1, min(round(box["width"] * inner_w), width - border - x))
    bh = max(1, min(round(box["height"] * inner_h), height - border - y))
    layer = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(1, round(page_width * 0.0015))
    body_h = max(1, round(bh * (0.84 if bubble["tail"] else 1)))
    if bubble["tail"]:
        draw.polygon([(round(bw * .45), max(0, body_h - stroke * 2)), (round(bw * .30), bh - 1),
                      (round(bw * .70), max(0, body_h - stroke * 2))], fill="white", outline="black", width=stroke)
    bounds = (0, 0, max(0, bw - 1), max(0, body_h - 1))
    if bubble["shape"] == "ellipse":
        draw.ellipse(bounds, fill="white", outline="black", width=stroke)
        inset_x, inset_y = max(2, round(bw * .18)), max(2, round(body_h * .18))
    else:
        draw.rectangle(bounds, fill="white", outline="black", width=stroke)
        inset_x, inset_y = max(2, round(bw * .10)), max(2, round(body_h * .10))
    text_w, text_h = bw - inset_x * 2, body_h - inset_y * 2
    try:
        if text_w < 1 or text_h < 1:
            raise TextLayoutError("吹き出しが小さすぎます。サイズを広げてください。")
        text_layer, _ = draw_fitted_text((text_w, text_h), content, bubble["flow"], font,
                                         max(1, round(page_width * bubble["font_scale"])), max(5, round(page_width * .008)))
        layer.alpha_composite(text_layer, (inset_x, inset_y))
    except TextLayoutError:
        draw.rectangle((0, 0, bw - 1, bh - 1), outline="red", width=max(1, stroke))
        image.paste(layer, (x, y), layer)
        raise
    image.paste(layer, (x, y), layer)


def _render_cell(panel, content, bubbles, width, height, page_width, border, font):
    image = Image.new("RGB", (width, height), "white")
    notes, errors = [], []
    loaded = False
    if content:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as source:
                    if source.format not in ("PNG", "JPEG", "WEBP") or source.width * source.height > 24_000_000 or getattr(source, "is_animated", False):
                        raise ValueError
                    source = ImageOps.exif_transpose(source).convert("RGBA")
                    fitted = ImageOps.contain(source, (max(1, width - border * 2), max(1, height - border * 2)), Image.Resampling.LANCZOS)
                    image.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2), fitted)
                    loaded = True
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            notes.append("採用画像を解読できないためプレースホルダーを表示します。")
    if not loaded:
        ImageDraw.Draw(image).rectangle((border, border, max(border, width - border - 1), max(border, height - border - 1)), fill="#eeeeee")
        try:
            label, _ = draw_fitted_text((max(1, width - border * 2), max(1, height - border * 2)),
                                        f"{panel['panel']}コマ\n画像なし", "horizontal", font, max(8, round(page_width * .025)), 4)
            image.paste(label, (border, border), label)
        except TextLayoutError:
            pass
    for index, bubble in enumerate(bubbles, 1):
        text = bubble_text(bubble, panel)
        if bubble["visible"] and text.strip():
            try:
                _draw_bubble(image, bubble, text, page_width, border, font)
            except TextLayoutError as error:
                errors.append(f"{panel['panel']}コマ目・吹き出し{index}：{error}")
    if border:
        ImageDraw.Draw(image).rectangle((0, 0, width - 1, height - 1), outline="black", width=border)
    return png_bytes(image), tuple(errors), tuple(notes)


def render_cell(page_number, panel, config, artwork, store, frame_size, page_width, settings, font, cache):
    image = adopted_image(artwork, page_number, panel["panel"])
    notes = []
    content = None
    if image:
        if image_context_changed(image, artwork["panels"][panel_key(page_number, panel["panel"])], artwork):
            notes.append("画像登録後に作画設定が変わっています。画像と現在の指示の一致を確認してください。")
        try:
            content = store.image_bytes(image)
        except StorageError as error:
            notes.append(str(error) + " プレースホルダーを表示します。")
        if image["source_panel"] != panel:
            notes.append("ストーリー変更前の採用画像です。内容を確認してください。")
    else:
        notes.append("画像が未採用のためプレースホルダーを表示します。")
    bubbles = [bubble for bubble in config["bubbles"] if bubble["panel"] == panel["panel"]]
    border = round(page_width * settings["border"] / 100)
    key = hashlib.sha256(json.dumps(dict(panel=panel, bubbles=bubbles, size=frame_size, page_width=page_width,
                                         border=border, font=font.signature, image=hashlib.sha256(content).hexdigest() if content else None),
                                   ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    result = cache.get(key) if cache is not None else None
    if result is None:
        result = _render_cell(panel, content, bubbles, *frame_size, page_width, border, font)
        if cache is not None:
            cache.put(key, result)
    return result[0], result[1], tuple(notes) + result[2]


def layout_warnings(page, config, data):
    notes = []
    if config["source_page"] != page:
        notes.append("ストーリー変更後の配置です。吹き出しと本文の対応を確認してください。")
    saved = data["pages"].get(str(page["page"]))
    if saved and len(saved["source_page"]["panels"]) != len(page["panels"]):
        notes.append("コマ数が変わったため仮の自動配置で表示しています。配置を保存し直してください。以前の設定は保持しています。")
    return notes


def render_page(page, version_id, artwork, data, store, font, cache=None, width=None):
    settings = data["settings"]
    page_width, page_height = dimensions(settings, width)
    config = effective_page(data["pages"].get(str(page["page"])), data["project_id"], page, version_id)
    canvas = Image.new("RGB", (page_width, page_height), "white")
    errors, notes = [], layout_warnings(page, config, data)
    for panel, frame in zip(page["panels"], pixel_frames(page, config, settings, width)):
        png, cell_errors, cell_notes = render_cell(page["page"], panel, config, artwork, store,
                                                   (frame["width"], frame["height"]), page_width, settings, font, cache)
        with Image.open(BytesIO(png)) as image:
            canvas.paste(image, (frame["x"], frame["y"]))
        errors.extend(cell_errors)
        notes.extend(f"{panel['panel']}コマ目：{note}" for note in cell_notes)
    return RenderedPage(png_bytes(canvas, settings["dpi"]), page_width, page_height, tuple(errors), tuple(dict.fromkeys(notes)))
