"""実際の作品画像とは区別する、保存済みテスト画像を持つ実例プロジェクト。"""
import json
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw
from manga.models import Project
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from manga.artwork_storage import ArtworkStore
from manga.page_storage import PageStore

EXAMPLE=Path(__file__).resolve().parents[1]/"examples/page_editor/story.json"


def create_example(root,width=1080):
    story=json.loads(EXAMPLE.read_text())
    project=Project(title=story["title"],genre="青春",synopsis=story["summary"],page_count=1)
    ProjectStore(root).save(project)
    latest=StoryStore(root,project.id).save(story,expected_version_id=None)
    images=ArtworkStore(root,project.id)
    for panel,size in ((1,(500,850)),(2,(1000,500)),(3,(600,850))):
        # 大きく異なる比率・左右で異なる色。余白とトリミング位置を画素で検証できる。
        image=Image.new("RGB",size,"#f7ad7a");draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,size[0]//3,size[1]),fill="#6374a0")
        draw.rectangle((size[0]*2//3,0,size[0],size[1]),fill="#9f5966")
        draw.ellipse((size[0]*.37,size[1]*.30,size[0]*.62,size[1]*.52),fill="#e7b9a0",outline="#453844",width=4)
        draw.rectangle((size[0]*.39,size[1]*.53,size[0]*.64,size[1]),fill="#46506d")
        output=BytesIO();image.save(output,"PNG")
        candidate=images.add_panel_image(1,panel,output.getvalue(),f"saved_test_panel_{panel}.png",story_version_id=latest["id"],expected_revision=images.load()["revision"])
        images.select_panel_image(1,panel,candidate["id"],expected_revision=images.load()["revision"])
    store=PageStore(root,project.id)
    store.save_settings(dict(store.load()["settings"],width=width),expected_revision=0)
    (root/'.page-editor-test-fixture').write_text(project.id)
    return project,latest,images,store
