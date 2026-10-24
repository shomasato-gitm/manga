import hashlib
import socket

import pytest

from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_samples import create_image_example,confirm_bible


@pytest.fixture
def image_workspace(tmp_path,monkeypatch):
    directory=tmp_path/'data'/'projects'
    monkeypatch.setenv('MANGA_DATA_DIR',str(directory)); monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'runtime'/'budget.sqlite3'))
    monkeypatch.delenv('OPENAI_API_KEY',raising=False); monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    def forbidden(*a,**k): raise AssertionError('No network/API calls in image tests')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    return create_image_example(directory)


def generate(workspace,kind='panel',*,provider='mock',content=None,filename=None,new=False,**selection):
    _,service,_=workspace
    if kind=='panel' and not selection: selection=dict(page=1,panel=1)
    plan=service.plan(kind,**selection)
    job=service.create_job(plan,provider=provider,content=content,filename=filename,expected_revision=service.store.load()['revision'],new_request=new)
    return service.run(job['id'])


def adopt(workspace,candidate,**kwargs):
    _,service,_=workspace
    if not kwargs.pop('skip_review',False):
        service.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'テスト素材を人間の確認として明示。完成品質ではない。',expected_revision=service.store.load()['revision'])
    return service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],confirmed=True,**kwargs)


def references_ready(workspace):
    _,service,characters=workspace
    for person in service.artwork.load()['characters']:
        if not person['primary_reference_id']:
            candidate=generate(workspace,'character',character_id=person['id'])
            adopt(workspace,candidate)
            confirm_bible(characters,person['id'])


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
