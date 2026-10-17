"""『君が忘れた、最後の約束』の動作検証。画像は実作品とは異なるテスト用図形です。"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from page_editor_fixtures import create_example
from manga.page_layout import default_page
from manga.page_render import RenderCache,render_page
from manga.japanese_text import find_japanese_font
from manga.page_exports import export_book,export_vertical,page_png


def create_outputs(output):
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('空の出力フォルダを指定してください。既存ファイルは上書きしません。')
    with tempfile.TemporaryDirectory(prefix='manga-page-demo-') as temp:
        project,latest,images,store=create_example(Path(temp))
        page=latest['story']['pages'][0];data=store.load()
        config=default_page(project.id,page,latest['id'],data['settings'])
        config['image_placements']['1'].update(y=.35,zoom=1.15)
        store.save_page(1,config,story_version_id=latest['id'],expected_revision=data['revision'])
        data=store.load();artwork=images.load();font=find_japanese_font();cache=RenderCache()
        full=render_page(page,latest['id'],artwork,data,images,font,cache)
        preview=render_page(page,latest['id'],artwork,data,images,font,cache,width=600)
        pdf,notes=export_book(latest['story'],latest['id'],artwork,data,images,font,'pdf',cache)
        vertical,vertical_notes=export_vertical([page],latest['id'],artwork,data,images,font,600,cache)
        (output/'page_001.png').write_bytes(page_png(full));(output/'preview_600.png').write_bytes(preview.png)
        (output/'manga.pdf').write_bytes(pdf);(output/'manga_vertical.zip').write_bytes(vertical)
        report=dict(title=project.title,fixture_images='編集前に保存・採用したテスト用図形。実作品の画像やAI生成画像ではありません。',
                    page_size=[full.width,full.height],bubbles=[dict(panel=b['panel'],speaker=b['speaker'],text=b['text'],kind=b['kind']) for b in config['bubbles']],
                    errors=full.errors,warnings=tuple(notes)+tuple(vertical_notes))
        (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(output.resolve())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();create_outputs(args.output)
