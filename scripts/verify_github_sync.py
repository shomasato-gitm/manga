"""指定作品の読み取り・候補・採用・PNG/PDF・往復を検証。mainは変更しない。"""
import argparse
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from PIL import Image,ImageChops

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from manga.git_transport import GitRemote
from manga.github_sync import SyncService
from manga.sync_manifest import json_bytes,sha256,decode_json
from manga.artwork_storage import ArtworkStore
from manga.story_storage import StoryStore
from manga.page_storage import PageStore
from manga.page_render import render_page
from manga.page_exports import export_book,page_png
from manga.japanese_text import find_japanese_font

WORK_ID='2d83c11f-04ee-4134-a66d-dace81c6f698'


def verify(output,github=False,branch=None):
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('空の検証専用フォルダを指定してください。既存データは上書きしません。')
    prefix=f'works/{WORK_ID}/'
    if github:
        branch=branch or f'studio-sync-test/{WORK_ID}-{uuid4().hex[:8]}'
        if not branch.startswith('studio-sync-test/'):raise ValueError('実GitHub検証は専用のstudio-sync-test/ブランチに限定します。')
        remote=GitRemote('shomasato-gitm/manga',output/'git_cache',os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN'))
        source_branch='main';source_commit=remote.head('main')
    else:
        bare=output/'remote.git';subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
        remote=GitRemote.for_local_test(bare,output/'git_cache');source_branch=branch='exchange'
        source_dir=ROOT/'works'/WORK_ID
        original={prefix+p.name:p.read_bytes() for p in source_dir.iterdir() if p.is_file()}
        source_commit=remote.publish(branch,None,None,original,'Read-only copy of supplied work for sync validation')
    names=remote.list_files(source_commit,prefix)
    original={path:remote.read(source_commit,path) for path in names if '/sync/' not in path}
    composition=decode_json(original[prefix+'page_01_composition_manifest_2026-10-17.json'])
    bindings=[dict(path=prefix+i['file'],page=1,panel=i['panel']) for i in composition['sources']]
    service=SyncService(output/'client',WORK_ID,remote)
    candidate=service.fetch_legacy(source_branch,prefix+'story_test_revision_2026-10-17.json',
        artwork_path=prefix+'artwork_directions_test_2026-10-17.json',images=bindings,
        attachments=[prefix+'editorial_test_review_2026-10-17.json',prefix+'page_01_composition_manifest_2026-10-17.json'],expected_revision=0)
    assert not (service.store.root/'story_history.json').exists()
    service.adopt(candidate['id'],['story','settings','images'],expected_revision=1,adopt_selections=True)
    art=ArtworkStore(service.directory,WORK_ID);latest=StoryStore(service.directory,WORK_ID).load_history()[-1]
    pages=PageStore(service.directory,WORK_ID);data=pages.load()
    pages.save_settings(dict(data['settings'],width=600),expected_revision=0);data=pages.load()
    font=find_japanese_font();artwork=art.load()
    png=page_png(render_page(latest['story']['pages'][0],latest['id'],artwork,data,art,font))
    pdf,notes=export_book(latest['story'],latest['id'],artwork,data,art,font,'pdf')
    plan=service.prepare_export(branch,expected_revision=2,source_branch=source_branch,outputs={'page_001.png':png,'book.pdf':pdf})
    pushed=service.push(plan['id'],expected_revision=3)
    receiver=SyncService(output/'receiver',WORK_ID,remote)
    received=receiver.fetch_bundle(branch,expected_revision=0)
    receiver.adopt(received['id'],['story','settings','images'],expected_revision=1,adopt_selections=True)
    other=ArtworkStore(receiver.directory,WORK_ID);pictures=[]
    for key,entry in artwork['panels'].items():
        for image in entry['candidates']:
            content=art.image_bytes(image);assert other.image_bytes(image)==content
            with Image.open(BytesIO(content)) as decoded:
                red,green,blue=decoded.convert('RGB').split()
                assert ImageChops.difference(red,green).getbbox() or ImageChops.difference(green,blue).getbbox()
            pictures.append(dict(page=entry['page'],panel=entry['panel'],image_id=image['id'],sha256=sha256(content)))
    assert len(pictures)==3 and len(latest['story']['pages'])==8
    assert [c['id'] for c in artwork['characters']]==[c['id'] for c in other.load()['characters']]
    assert all(remote.read(pushed['commit'],path)==content for path,content in original.items())
    returned=receiver._files(received)
    assert returned['exports/page_001.png']==png and returned['exports/book.pdf']==pdf
    (output/'page_001.png').write_bytes(png);(output/'book.pdf').write_bytes(pdf)
    report=dict(passed=True,mode='GitHub HTTPS Git' if github else 'temporary bare Git',repository=remote.repository,
        work_id=WORK_ID,title=latest['story']['title'],source_commit=source_commit,branch=branch,commit=pushed['commit'],
        story_pages=8,panels=24,color_images=pictures,character_ids=[c['id'] for c in artwork['characters']],
        original_files_preserved=True,main_written=False,automatic_adoption=False,outputs=['page_001.png','book.pdf'],
        limitations=['基準画像の正式な採用関係は元資料でも未確認。人物の顔の一致を自動確認していない。',
                     '提供画像は1ページ3コマのみ。PDFの他ページは未採用画像のプレースホルダー。完成漫画と判定していない。'],warnings=notes)
    (output/'verification.json').write_bytes(json_bytes(report));print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--github',action='store_true',help='専用ブランチを新規作成して実GitHub往復を検証する（明示操作）')
    parser.add_argument('--branch');args=parser.parse_args();verify(args.output,args.github,args.branch)
