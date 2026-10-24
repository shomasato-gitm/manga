"""3人物＋ナレーター、8ページ24コマ。外部通信を禁止した独立fixture。"""
import hashlib
import io
import socket

import pytest
from PIL import Image

from manga.artwork_storage import ArtworkStore
from manga.character_ai_service import CharacterAIService
from manga.editorial_storage import EditorialStore
from manga.models import Project
from manga.storage import ProjectStore, write_json_atomic
from manga.story_storage import StoryStore
from manga.voice_models import empty_voice


def image_bytes():
    stream = io.BytesIO(); Image.new('RGB', (32, 48), 'orange').save(stream, 'PNG')
    return stream.getvalue()


def build_workspace(directory):
    from manga.character_ai_samples import create_character_example
    return create_character_example(directory)


@pytest.fixture
def character_workspace(tmp_path, monkeypatch):
    directory = tmp_path/'data'/'projects'
    monkeypatch.setenv('MANGA_DATA_DIR', str(directory))
    monkeypatch.setenv('MANGA_BUDGET_DB', str(tmp_path/'runtime'/'budget.sqlite3'))
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('MANGA_ENABLE_PAID_CHARACTER', raising=False)
    def forbidden(*args, **kwargs): raise AssertionError('No API/network calls in Phase 2-C tests')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    return build_workspace(directory)


def generate(workspace, **kwargs):
    project, service = workspace
    return service.proposals.create_mock(project, service.proposals.latest()['id'], expected_revision=service.proposals.load()['revision'], **kwargs)


def adopt(workspace, result, index=0, *, fields=None, existing_id=None, values=None, **kwargs):
    _, service = workspace
    item = result['proposal']['payload']['items'][index]
    fields = fields if fields is not None else [k for k,v in item['fields'].items() if v]
    return service.proposals.adopt_fields(result['proposal']['id'], item['id'], values or item['fields'], fields, existing_id,
        expected_revision=service.proposals.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'],
        expected_editorial_revision=service.proposals.editorial.load()['revision'], expected_character_revision=service.store.load()['revision'], identity_confirmed=True, **kwargs)


def bible(workspace, identifier=None, **kwargs):
    _, service = workspace
    person = service.artwork.load()['characters'][0] if identifier is None else service.artwork._character(service.artwork.load(), identifier)
    return service.save_bible(person['id'], version_id=service.proposals.latest()['id'], expected_revision=service.store.load()['revision'],
        expected_artwork_revision=service.artwork.load()['revision'], locked_fields=kwargs.pop('locked_fields', ['hair_color', 'clothing']),
        aliases=kwargs.pop('aliases', []), exceptions=kwargs.pop('exceptions', []), reason='設定と参照画像を人間が確認したテスト', confirmed=True, **kwargs)


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
