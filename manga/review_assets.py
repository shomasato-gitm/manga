"""目視レビュー用の縮小画像と手動添付用ZIP。元画像・制作JSONは変更しない。"""
from collections import OrderedDict
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageDraw, ImageOps

from manga.artwork_storage import ArtworkStore
from manga.japanese_text import find_japanese_font
from manga.page_exports import page_png, zip_files, MAX_EXPORT_PIXELS
from manga.page_layout import dimensions
from manga.page_render import render_page, adopted_image, decode_image, RenderCache, png_bytes
from manga.panel_downloads import original_bytes
from manga.storage import StorageError
from manga.ui_data import cached_read

_PREVIEWS = OrderedDict()
_PREVIEW_BUDGET = 8*1024*1024


def page_preview(context, number, *, width=600, cache=None):
    """完成解像度で描画後に縮小。ページ指紋・フォント変更時は必ず再描画する。"""
    key = (str(context.root), number, context.snapshot['pages'][str(number)]['fingerprint'], width)
    if key in _PREVIEWS:
        _PREVIEWS.move_to_end(key)
        return _PREVIEWS[key]
    if not context.composition or not context.artwork: raise ValueError('ページ編集・作画設定を読み込めないため描画できません。')
    page = next(p for p in context.pages if p['page'] == number)
    result = render_page(page, context.latest['id'], context.artwork, context.composition,
        ArtworkStore(context.root.parent, context.project_id), find_japanese_font(context.composition['settings']['font_path']), cache, width=width)
    if len(result.png) <= _PREVIEW_BUDGET:
        _PREVIEWS[key] = result
        while len(_PREVIEWS) > 24 or sum(len(r.png) for r in _PREVIEWS.values()) > _PREVIEW_BUDGET:
            _PREVIEWS.popitem(last=False)
    return result


def thumbnail_path(context, number):
    fingerprint = context.snapshot['pages'][str(number)]['fingerprint']
    return context.root / 'review_previews' / f'page_{number:03d}_{fingerprint}.png'


def save_thumbnails(context, *, progress=None, cache=None):
    """明示操作時だけ全ページを順番に描画。ディスクにも縮小PNGだけを保持する。"""
    cache = cache if cache is not None else RenderCache()
    errors = []
    for done, page in enumerate(context.pages, 1):
        path = thumbnail_path(context, page['page'])
        if not path.is_file():
            temporary = None
            try:
                result = page_preview(context, page['page'], width=180, cache=cache)
                path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as file:
                    temporary = Path(file.name)
                    file.write(result.png); file.flush(); os.fsync(file.fileno())
                os.replace(temporary, path)
            except (ValueError, OSError, StorageError) as error:
                errors.append(f'{page["page"]}ページ：{error}')
            finally:
                if temporary is not None: temporary.unlink(missing_ok=True)
        if progress: progress(done, len(context.pages))
    return errors


def read_thumbnail(context, number):
    path = thumbnail_path(context, number)
    return cached_read(path, path.read_bytes, variant='quality_thumbnail') if path.is_file() else None


def comparison_png(context, targets):
    """選んだコマだけを1コマずつ縮小して合成。顔や文字の画像認識はしない。"""
    if not targets or len(targets) > 24: raise ValueError('比較画像は1〜24コマを選択してください。')
    width, height = 320, 360
    columns = min(3, len(targets)); rows = (len(targets)+columns-1)//columns
    canvas = Image.new('RGB', (columns*width, rows*height), 'white')
    draw = ImageDraw.Draw(canvas); notes = []
    store = ArtworkStore(context.root.parent, context.project_id)
    for index, target in enumerate(targets):
        p, c = target['page'], target['panel']
        x, y = (index % columns)*width, (index//columns)*height
        draw.text((x+10, y+8), f'Page {p} / Panel {c}', fill='black')
        image = adopted_image(context.artwork, p, c)
        try:
            if image is None: raise ValueError('画像未採用')
            with decode_image(original_bytes(store, image)) as source:
                fitted = ImageOps.contain(source.convert('RGB'), (width-16, height-40), Image.Resampling.LANCZOS)
                canvas.paste(fitted, (x+(width-fitted.width)//2, y+32+(height-40-fitted.height)//2))
        except (ValueError, OSError, StorageError) as error:
            notes.append(f'{p}ページ・{c}コマ：{error}')
            draw.text((x+10, y+50), 'Image unavailable', fill='red')
    return png_bytes(canvas), notes


def review_package(context, request, prompt):
    if request['snapshot']['token'] != context.snapshot['token']: raise ValueError('制作データが変わりました。レビュー依頼を作成し直してください。')
    page_numbers = sorted({t['page'] for t in request['targets'] if t['panel'] is None} |
                          ({t['page'] for t in request['targets']} if request['include_pages'] else set()))
    width, height = dimensions(context.composition['settings'])
    if width*height*len(page_numbers) > MAX_EXPORT_PIXELS: raise ValueError('資料が大きすぎます。ページ数を減らしてください。')
    from manga.review_prompts import response_example
    files, notes = [('review_prompt.txt', prompt.encode('utf-8')),
                    ('response_format.json', json.dumps(response_example(request), ensure_ascii=False, indent=2).encode('utf-8'))], []
    cache = RenderCache()
    for number in page_numbers:
        rendered = page_preview(context, number, width=width, cache=cache)
        files.append((f'page_{number:03d}.png', page_png(rendered)))
        notes.extend(f'{number}ページ：{note}' for note in rendered.warnings)
    panels = [t for t in request['targets'] if t['panel'] is not None]
    if panels:
        content, messages = comparison_png(context, panels)
        files.append(('panel_comparison.png', content)); notes.extend(messages)
    by_id = {image['id']: image for c in context.artwork['characters'] if not c['deleted_at'] for image in c['references']}
    store = ArtworkStore(context.root.parent, context.project_id)
    for image_id in request['reference_ids']:
        if image_id not in by_id: raise ValueError('選択したキャラクター参照画像が見つかりません。依頼を作り直してください。')
        files.append((f'references/{image_id}.png', original_bytes(store, by_id[image_id])))
        if sum(len(content) for _, content in files) > 100*1024*1024: raise ValueError('レビュー資料は100MB以内にしてください。参照画像を減らしてください。')
    manifest = dict(request=request, files=[name for name, _ in files], warnings=notes,
                    instruction='添付対象を確認して、ChatGPTへ手動で送信してください。画像のない箇所は評価できません。')
    files.append(('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')))
    return zip_files(files), notes
