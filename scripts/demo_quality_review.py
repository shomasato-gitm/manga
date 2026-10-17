"""空の専用フォルダに8ページ24コマの架空作品と品質確認資料を作る。ユーザーデータは扱わない。"""
import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from review_fixtures import create_review_example
from manga.review_context import load_context
from manga.review_checks import scan
from manga.review_assets import save_thumbnails, read_thumbnail, review_package
from manga.review_prompts import new_request, review_prompt, response_example
from manga.review_storage import ReviewStore
from manga.japanese_text import find_japanese_font
from manga.page_exports import export_book, export_vertical
from manga.page_render import RenderCache


def create_outputs(output):
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()): raise ValueError('空の専用フォルダを指定してください。既存ファイルは上書きしません。')
    directory = output/'test_data'; directory.mkdir()
    project, latest, artwork, pages = create_review_example(directory)
    context = load_context(directory, project.id)
    store = ReviewStore(directory, project.id)
    timings = {}
    def measure(name, action):
        start = time.perf_counter(); result = action()
        timings[name] = round((time.perf_counter()-start)*1000,2)
        return result
    cache = RenderCache()
    result = measure('scan_8_pages_24_panels_ms', lambda:scan(context, sns_width=600, cache=cache))
    store.save_scan(result, expected_revision=0)
    messages = measure('create_8_thumbnails_ms', lambda:save_thumbnails(context, cache=cache))
    if messages: raise ValueError(' / '.join(messages))
    with patch('manga.review_assets.original_bytes', side_effect=AssertionError('再表示で元画像を読んだ')):
        measure('reuse_8_thumbnails_ms', lambda:[read_thumbnail(context,n) for n in range(1,9)])
        measure('reuse_thumbnail_generation_ms', lambda:save_thumbnails(context))
    references = [artwork.load()['characters'][0]['references'][0]['id']]
    request = new_request(context, [dict(page=1,panel=1),dict(page=1,panel=2),dict(page=1,panel=3)], references,
                          '話者別吹き出し・視線誘導・人物の一貫性を確認', include_pages=True)
    request['prompt'] = review_prompt(project, context, request)
    store.save_request(request, expected_revision=1)
    package, notes = review_package(context, request, request['prompt'])
    (output/'image_review_materials.zip').write_bytes(package)
    response = response_example(request)
    response['issues'][0].update(category='テスト用の目視確認候補', message='人物の外見が統一されているか確認してください。', reason='テスト用の模擬回答です。実際のAIによる画像評価ではありません。')
    (output/'sample_ai_response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2)+'\n')
    store.import_ai(json.dumps(response, ensure_ascii=False), expected_revision=2, snapshot=context.snapshot)
    font = find_japanese_font(pages.load()['settings']['font_path'])
    pdf, _ = export_book(latest['story'], latest['id'], artwork.load(), pages.load(), artwork, font, 'pdf', cache)
    png, _ = export_book(latest['story'], latest['id'], artwork.load(), pages.load(), artwork, font, 'png_zip', cache)
    sns, _ = export_vertical(latest['story']['pages'], latest['id'], artwork.load(), pages.load(), artwork, font, 600, cache)
    for filename, content in [('manga.pdf',pdf),('manga_pages.zip',png),('manga_vertical.zip',sns)]: (output/filename).write_bytes(content)
    report = dict(title=project.title, pages=8, panels=24, images='すべてローカルで描いたテスト用図形を保存・採用。実作品の画像ではありません。',
                  source='ユーザーデータ・API・実際のChatGPT返答は使用していません。', checks_completed=result['completed'],
                  render_errors=[i for i in result['issues'] if i['blocks_export']],
                  detected_issues=len(result['issues']), timings=timings, reused_thumbnails_original_reads=0, warnings=notes,
                  test_data=str(directory.resolve()))
    (output/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    create_outputs(parser.parse_args().output)
