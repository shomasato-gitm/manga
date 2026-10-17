"""保存済み検証画像で合成キャッシュを実測する。利用者データには触れない。"""
import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from page_editor_fixtures import create_example
from manga.japanese_text import find_japanese_font
from manga.page_layout import default_page
from manga.page_render import render_page,RenderCache
from manga.ui_data import clear_cache


def measure():
    with tempfile.TemporaryDirectory(prefix='manga-stage8-measure-') as temp:
        project,latest,images,store=create_example(Path(temp))
        data=store.load();page=latest['story']['pages'][0];artwork=images.load()
        data['pages']['1']=default_page(project.id,page,latest['id'],data['settings'])
        original_files={p:p.read_bytes() for p in images.root.rglob('*.png')}
        font=find_japanese_font();cache=RenderCache();clear_cache()
        reads=[];original_read=images.image_bytes
        def counted(image):reads.append(image['id']);return original_read(image)
        images.image_bytes=counted
        def run():
            started=time.perf_counter();rendered=render_page(page,latest['id'],artwork,data,images,font,cache,width=600)
            assert not rendered.errors,rendered.errors
            return round((time.perf_counter()-started)*1000,2)
        cold=run();cold_reads=len(reads);reads.clear()
        warm=[run() for _ in range(9)];warm_reads=len(reads);reads.clear()
        before=cache.misses
        data['pages']['1']['image_placements']['2']['x']=.7
        edited=run()
        assert cache.misses-before==1
        assert all(p.read_bytes()==content for p,content in original_files.items())
        return dict(python=platform.python_version(),platform=platform.system(),size='1080x1620から600x900へ縮小',
                    conditions='日本語フォント検出後のエンジン実測。保存済みテスト画像3枚。ブラウザ・操作・ファイル保存時間を含まない。',
                    cold_ms=cold,cold_image_reads=cold_reads,warm_median_ms=statistics.median(warm),warm_image_reads=warm_reads,
                    one_panel_edit_ms=edited,edited_panel_cache_misses=cache.misses-before,edit_image_reads=len(reads),warm_samples_ms=warm)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=measure();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
