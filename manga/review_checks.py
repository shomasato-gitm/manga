"""保存済みデータを検査する。描画・フォント・配置は既存の検証を再利用する。"""
from uuid import UUID, uuid4, uuid5

from manga.artwork_models import image_context_changed, panel_key
from manga.japanese_text import find_japanese_font, TextLayoutError
from manga.models import now_iso
from manga.page_dialogue import split_dialogue, SPEAKER
from manga.page_layout import bubble_text, effective_page, pixel_frames, validate_page
from manga.page_images import placement_for, image_scale
from manga.page_render import adopted_image, decode_image, render_cell, RenderCache
from manga.panel_downloads import original_bytes
from manga.artwork_storage import ArtworkStore
from manga.storage import StorageError

CHECKS = ['ストーリー・作画指示・人物ID', '採用画像・ファイル・復号・鮮度', '実出力ピクセルに対する解像度',
          'コマ配置・吹き出し・話者分割', '本番描画・文字収まり・フォント対応']


def make_issue(project_id, category, message, *, page=None, panel=None, severity='中', target='image',
               reason='保存済みデータの検査で検出しました。', suggestion='対象の編集画面で内容を確認・修正し、再検査してください。', blocks_export=False):
    identity = f'{page}:{panel}:{category}:{message}'
    return dict(id=str(uuid5(UUID(project_id), identity)), page=page, panel=panel, category=category,
                message=message, severity=severity, target=target, reason=reason, suggestion=suggestion, blocks_export=blocks_export)


def scan(context, *, sns_width=None, progress=None, cache=None):
    """画像はコマごとに復号・解放。SNS選択時は実際の拡大率も加味する。"""
    if sns_width not in (None, 600, 1080): raise ValueError('SNS幅は600か1080pxを指定してください。')
    result = dict(id=str(uuid4()), created_at=now_iso(), snapshot=context.snapshot, pages=[p['page'] for p in context.pages],
                  issues=[], completed=False, checks=[], rendered_pages=[], sns_width=sns_width)
    def issue(category, message, **fields):
        result['issues'].append(make_issue(context.project_id, category, message, **fields))
    for error in context.errors:
        issue('データ読込エラー', error['message'], target='layout' if error['source'] == 'composition' else 'story' if error['source'] == 'story' else 'direction', severity='高', blocks_export=True)
    if not context.latest:
        issue('ストーリー未保存', 'ストーリーが保存されていません。', target='story', severity='高')
        return result
    if not context.artwork or not context.composition:
        return result
    art, data = context.artwork, context.composition
    store = ArtworkStore(context.root.parent, context.project_id)
    active_ids = {c['id'] for c in art['characters'] if not c['deleted_at']}
    font = None
    try:
        font = find_japanese_font(data['settings']['font_path'])
    except TextLayoutError as error:
        issue('フォント', str(error), target='settings', severity='高', blocks_export=True)
    from manga.page_exports import validate_book_plan, vertical_plan
    try:
        validate_book_plan(context.pages, data['settings'])
        if sns_width: vertical_plan(context.pages, context.latest['id'], data, sns_width)
    except ValueError as error:
        issue('全ページ出力計画', str(error), target='settings', severity='高')
    cache = cache if cache is not None else RenderCache()
    complete = font is not None
    for done, page in enumerate(context.pages, 1):
        n = page['page']
        saved = data['pages'].get(str(n))
        config = effective_page(saved, context.project_id, page, context.latest['id'], data['settings'])
        try:
            validate_page(config)
        except (ValueError, KeyError, TypeError) as error:
            issue('配置不正', str(error), page=n, target='layout', severity='高', blocks_export=True)
            complete = False
            if progress: progress(done, len(context.pages))
            continue
        if not saved:
            issue('ページ未保存', '標準の仮配置です。ページ編集を確認・保存してください。', page=n, target='layout')
        elif saved['source_page'] != page or saved['source_version_id'] != context.latest['id']:
            issue('古いページ編集', '別のストーリー版に基づくページ編集です。', page=n, target='layout')
        frames = pixel_frames(page, config, data['settings'])
        page_rendered = font is not None
        for panel, frame in zip(page['panels'], frames):
            c = panel['panel']; entry = art['panels'].get(panel_key(n, c), {})
            direction = entry.get('direction')
            fields = dict(page=n, panel=c)
            if not direction:
                issue('作画指示未設定', '作画指示が設定されていません。', target='direction', **fields)
            else:
                if direction['source_panel'] != panel or direction['story_version_id'] != context.latest['id']:
                    issue('古い作画指示', '現在のストーリー版と作画指示の元データが異なります。', target='direction', **fields)
                for character_id in direction['character_ids']:
                    if character_id not in active_ids:
                        issue('人物ID不正', f'人物ID {character_id} は存在しないか削除済みです。', target='direction', **fields)
            image = adopted_image(art, n, c)
            if image is None:
                issue('画像未採用', 'このコマの画像が未採用です。', **fields)
            else:
                if image['story_version_id'] != context.latest['id'] or image['source_panel'] != panel or image_context_changed(image, entry, art):
                    issue('古い採用画像', '画像登録時と現在のストーリー・作画設定が異なります。', **fields)
                try:
                    with decode_image(original_bytes(store, image)) as source:
                        border = round(data['settings']['width'] * data['settings']['border'] / 100)
                        inner = (max(1, frame['width'] - border*2), max(1, frame['height'] - border*2))
                        scale = image_scale(source.size, inner, placement_for(config, c))
                        display_scale = max(1, sns_width / frame['width']) if sns_width else 1
                        if scale * display_scale > 1.000001:
                            target_label = f'ページ横幅{data["settings"]["width"]}px' + (f'・SNS横幅{sns_width}px' if sns_width else '')
                            issue('解像度不足', f'元画像{source.width}×{source.height}pxに対し、{target_label}の表示で約{scale*display_scale:.2f}倍の拡大が必要です。',
                                  reason=f'枠内{inner[0]}×{inner[1]}px、表示方式とズームを含む実際の表示倍率で判定しています。', **fields)
                except StorageError as error:
                    issue('画像ファイル欠損', str(error), severity='高', **fields)
                except (ValueError, OSError) as error:
                    issue('画像読込不良', f'採用画像を正常に読み込めません：{error}', severity='高', **fields)
            visible = [b for b in config['bubbles'] if b['panel'] == c and b['visible']]
            for bubble in visible:
                content = bubble_text(bubble, panel)
                if not content.strip() and (bubble['source'] == 'custom' or bubble['text'] is not None or panel.get(bubble['source'], '').strip()):
                    issue('空のセリフ', '表示する吹き出しの本文が空です。', target='bubble', **fields)
                utterances = split_dialogue(content) if bubble['source'] != 'narration' else []
                if len(utterances) > 1 or (any(u.needs_review for u in utterances) and (not bubble.get('speaker') or SPEAKER.search(content))) or (content.strip() and bubble.get('needs_review')):
                    issue('未分割セリフ', '複数発言または分割未確認の本文が残っています。自動分割案と原文を確認してください。', target='bubble', **fields)
            if panel['dialogue'].strip() and not any(b['source'] in ('dialogue', 'custom') and bubble_text(b, panel).strip() for b in visible):
                issue('セリフ非表示', '脚本のセリフに対応する表示中の吹き出しがありません。意図した省略か確認してください。', target='bubble', **fields)
            if font:
                try:
                    _, errors, notes = render_cell(n, panel, config, art, store, (frame['width'], frame['height']), data['settings']['width'], data['settings'], font, cache)
                    for message in errors:
                        issue('描画エラー', message, target='bubble', severity='高', blocks_export=True, **fields)
                    for message in notes:
                        issue('描画警告', message, target='bubble' if '文字サイズ' in message else 'image', severity='低', **fields)
                    if errors: page_rendered = False
                except (ValueError, OSError, StorageError) as error:
                    issue('描画エラー', str(error), target='layout', severity='高', blocks_export=True, **fields)
                    page_rendered = complete = False
        if page_rendered: result['rendered_pages'].append(n)
        if progress: progress(done, len(context.pages))
    result['completed'] = complete and not context.errors
    result['checks'] = CHECKS if complete else CHECKS[:-1]
    result['issues'] = list({i['id']: i for i in result['issues']}.values())
    return result
