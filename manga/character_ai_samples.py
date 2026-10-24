"""専用の新規作品だけに作る3人物・ナレーターのモック資料。実ユーザーデータは使わない。"""
import io

from PIL import Image

from manga.artwork_storage import ArtworkStore
from manga.character_ai_service import CharacterAIService
from manga.editorial_storage import EditorialStore
from manga.models import Project
from manga.storage import ProjectStore, write_json_atomic
from manga.story_storage import StoryStore
from manga.voice_models import empty_voice


def example_image():
    stream = io.BytesIO()
    Image.new('RGB', (32, 48), 'orange').save(stream, 'PNG')
    return stream.getvalue()


def create_character_example(directory):
    project = Project(title='君が忘れた、最後の約束（人物モック）', genre='青春', synopsis='3人が最後の約束を確かめる。', page_count=8)
    ProjectStore(directory).save(project)
    pages = []
    for page in range(1, 9):
        cells = []
        for panel, name in enumerate(('悠真', '美咲', '蓮'), 1):
            cells.append(dict(panel=panel, description=f'{name}が夕暮れの教室で窓を見つめる。' + ('髪色：黒、服装：制服' if panel == 1 else ''),
                              dialogue=f'{name}：「あの約束を覚えている？」', narration='卒業まで、あと7日。ずっと言えなかったことがある。' if panel == 1 else ''))
        pages.append(dict(page=page, summary=f'約束を確かめる{page}ページ。', panels=cells))
    story = dict(schema_version=1, title=project.title, summary=project.synopsis, pages=pages)
    version = StoryStore(directory, project.id).save(story, expected_version_id=None)
    EditorialStore(directory, project.id).finalize(expected_version_id=version['id'], expected_revision=0)
    art = ArtworkStore(directory, project.id)
    person = art.save_character(dict(name='悠真', hair_color='黒', clothing='制服', personality='慎重'), expected_revision=0)
    art.add_reference(person['id'], example_image(), '悠真_正面.png', '正面', expected_revision=art.load()['revision'])
    art.save_direction(1, 1, dict(character_ids=[person['id']], composition='悠真：髪色=黒、服装=制服'), story_version_id=version['id'], expected_revision=art.load()['revision'])
    candidate = art.add_panel_image(1, 1, example_image(), '既存画像.png', story_version_id=version['id'], expected_revision=art.load()['revision'])
    art.select_panel_image(1, 1, candidate['id'], expected_revision=art.load()['revision'])
    root = directory/project.id
    write_json_atomic(root/'voice.json', empty_voice(project.id))
    for folder in ('voice', 'video'):
        path = root/folder/'preserve.bin'; path.parent.mkdir(exist_ok=True); path.write_bytes(b'preserve '+folder.encode())
    return project, CharacterAIService(directory, project.id)
