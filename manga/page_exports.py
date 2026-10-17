"""ページPNG・全ページPDF・読み順通りの縦読みPNGを生成する。元画像には書き込まない。"""

import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from manga.page_layout import dimensions, effective_page
from manga.page_render import RenderCache, layout_warnings, pixel_frames, png_bytes, render_cell, render_page


MAX_EXPORT_PIXELS = 60_000_000
SNS_MAX_HEIGHT = 8000


def _budget(pixels):
    if pixels > MAX_EXPORT_PIXELS:
        raise ValueError("一度の出力は合計6000万画素までです。出力横幅を小さくするか、表示中のページだけを出力してください。")


def _check_errors(errors):
    if errors:
        raise ValueError("文字の収まり・フォントにエラーがあるため出力できません。" + " / ".join(errors[:4]))


def page_png(rendered):
    _check_errors(rendered.errors)
    return rendered.png


def export_book(story, version_id, artwork, data, store, font, kind="pdf", cache=None, progress=None):
    if kind not in ("pdf", "png_zip"):
        raise ValueError("出力形式が不正です。")
    width, height = dimensions(data["settings"])
    _budget(width * height * len(story["pages"]))
    cache = cache if cache is not None else RenderCache()
    frames, files, notes = [], [], []
    try:
        for done, page in enumerate(story["pages"],1):
            rendered = render_page(page, version_id, artwork, data, store, font, cache)
            _check_errors(rendered.errors)
            if progress:progress(done,len(story["pages"]))
            notes.extend(f"{page['page']}ページ：{note}" for note in rendered.warnings)
            if kind == "pdf":
                with Image.open(BytesIO(rendered.png)) as image:
                    frames.append(image.convert("RGB"))
            else:
                files.append((f"page_{page['page']:03d}.png", rendered.png))
        if not story["pages"]:
            raise ValueError("出力するページがありません。")
        if kind == "pdf":
            output = BytesIO()
            frames[0].save(output, format="PDF", save_all=True, append_images=frames[1:], resolution=data["settings"]["dpi"])
            return output.getvalue(), tuple(dict.fromkeys(notes))
        return zip_files(files), tuple(dict.fromkeys(notes))
    finally:
        for image in frames:
            image.close()


def zip_files(files):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(name, content)
    return output.getvalue()


def export_vertical(pages, version_id, artwork, data, store, font, width=1080, cache=None, progress=None):
    """ページ順・コマ番号順に1列へ。8000px以内でコマを切らずに分割する。"""
    if width not in (600, 1080):
        raise ValueError("縦読みの横幅は600または1080pxにしてください。")
    cache = cache if cache is not None else RenderCache()
    gap = round(width * data["settings"]["gutter"] / 100)
    plans, pixels, notes = [], 0, []
    for page in pages:
        config = effective_page(data["pages"].get(str(page["page"])), data["project_id"], page, version_id)
        notes.extend(f"{page['page']}ページ：{note}" for note in layout_warnings(page, config, data))
        for panel, frame in zip(page["panels"], pixel_frames(page, config, data["settings"])):
            height = max(1, round(width * frame["height"] / frame["width"]))
            if height > SNS_MAX_HEIGHT:
                raise ValueError("縦読みで1コマが8000pxを超えます。横幅を下げるか、コマの縦横比を調整してください。")
            plans.append((page, panel, config, height))
            pixels += width * (height + gap)
    _budget(pixels)
    if not plans:
        raise ValueError("出力するコマがありません。")
    groups, current, height = [], [], 0
    for plan in plans:
        required = plan[3] + (gap if current else 0)
        if current and height + required > SNS_MAX_HEIGHT:
            groups.append((current, height))
            current, height = [], 0
        height += plan[3] + (gap if current else 0)
        current.append(plan)
    groups.append((current, height))
    files, order = [], []
    done=0
    for index, (group, height) in enumerate(groups, 1):
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for page, panel, config, cell_height in group:
            png, errors, warnings = render_cell(page["page"], panel, config, artwork, store, (width, cell_height), width, data["settings"], font, cache)
            _check_errors(errors)
            done+=1
            if progress:progress(done,len(plans))
            with Image.open(BytesIO(png)) as image:
                canvas.paste(image, (0, y))
            notes.extend(f"{page['page']}ページ・{panel['panel']}コマ：{note}" for note in warnings)
            order.append(dict(file=f"vertical_{index:03d}.png", page=page["page"], panel=panel["panel"], y=y, height=cell_height))
            y += cell_height + gap
        files.append((f"vertical_{index:03d}.png", png_bytes(canvas, data["settings"]["dpi"])))
    files.append(("reading_order.json", json.dumps(order, ensure_ascii=False, indent=2).encode("utf-8")))
    return zip_files(files), tuple(dict.fromkeys(notes))
