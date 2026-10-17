"""8ページ24コマの架空の制作済み作品。呼出側の一時フォルダだけに生成する。"""
import copy
import json
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw

from manga.models import Project
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from manga.artwork_storage import ArtworkStore
from manga.page_storage import PageStore
from manga.page_layout import default_page


def sample_image(size=(900, 1000), color='#e6b697'):
    with Image.new('RGB', size, color) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, size[0]//4, size[1]), fill='#64779a')
        draw.ellipse((size[0]//3, size[1]//4, size[0]*2//3, size[1]//2), fill='#e9c9b6')
        output = BytesIO(); image.save(output, 'PNG'); return output.getvalue()


def create_review_example(root, *, page_count=8):
    source = json.loads((Path(__file__).resolve().parents[1]/'examples/page_editor/story.json').read_text())
    story = dict(title=source['title'], summary=source['summary'], pages=[])
    for n in range(1, page_count+1):
        page = copy.deepcopy(source['pages'][0]); page['page'] = n
        page['summary'] = f'約束をたどる{n}ページ目'
        if n > 1:
            for p in page['panels']:
                p['description'] = f'{n}ページ{p["panel"]}コマ。夕暮れの教室で約束を思い出す。'
                p['dialogue'] = f'悠真：「約束を忘れないよ」' if p['panel'] == 2 else ''
        story['pages'].append(page)
    project = Project(title=story['title'], genre='青春', synopsis=story['summary'], page_count=page_count)
    ProjectStore(root).save(project)
    stories = StoryStore(root, project.id); latest = stories.save(story, expected_version_id=None)
    art = ArtworkStore(root, project.id)
    cast = [art.save_character(dict(name=name, hairstyle='短髪' if name == '悠真' else '長髪', clothing='制服'), expected_revision=art.load()['revision']) for name in ('悠真', '美咲')]
    image = sample_image()
    art.add_reference(cast[0]['id'], image, 'synthetic_reference.png', '正面', expected_revision=art.load()['revision'])
    art.save_style('フルカラーの青春漫画。夕日の暖色。', expected_revision=art.load()['revision'])
    for page in latest['story']['pages']:
        for panel in page['panels']:
            n, c = page['page'], panel['panel']
            art.save_direction(n, c, dict(location='夕暮れの教室', expression='切なさ', character_ids=[x['id'] for x in cast]), story_version_id=latest['id'], expected_revision=art.load()['revision'])
            candidate = art.add_panel_image(n, c, image, f'synthetic_p{n}_c{c}.png', story_version_id=latest['id'], expected_revision=art.load()['revision'])
            art.select_panel_image(n, c, candidate['id'], expected_revision=art.load()['revision'])
    composition = PageStore(root, project.id)
    composition.save_settings(dict(composition.load()['settings'], width=600), expected_revision=0)
    for page in latest['story']['pages']:
        config = default_page(project.id, page, latest['id'], composition.load()['settings'])
        composition.save_page(page['page'], config, story_version_id=latest['id'], expected_revision=composition.load()['revision'])
    (root / '.quality-review-test-fixture').write_text(project.id)
    return project, latest, art, composition
