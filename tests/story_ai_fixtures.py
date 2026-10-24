"""新規tmp作品・架空料金・メモリ内APIのみ。実ネットワークは拒否する。"""
import json
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from manga.artwork_storage import ArtworkStore
from manga.storage import ProjectStore,write_json_atomic
from manga.story_storage import StoryStore
from manga.story_ai_service import StoryAIService
from manga.story_ai_mock import MockStoryAI
from manga.story_ai_provider import ResponsesStoryAI
from manga.story_ai_pricing import template,FLAGS


@pytest.fixture
def ai_workspace(tmp_path,monkeypatch,manga_project,story):
    directory=tmp_path/'data'/'projects';monkeypatch.setenv('MANGA_DATA_DIR',str(directory))
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'runtime'/'budget.sqlite3'))
    monkeypatch.delenv('MANGA_ENABLE_PAID_STORY',raising=False);monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    def forbidden(*a,**k):raise AssertionError('Real network/API calls are prohibited')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    ProjectStore(directory).save(manga_project);StoryStore(directory,manga_project.id).save(story,expected_version_id=None)
    art=ArtworkStore(directory,manga_project.id)
    for name in ('悠真','美咲'):art.save_character({'name':name,'hair_color':'黒'},expected_revision=art.load()['revision'])
    root=directory/manga_project.id
    for name in ('voice','video','images'):
        path=root/name/'existing.bin';path.parent.mkdir(exist_ok=True);path.write_bytes(b'preserve existing '+name.encode())
    from manga.voice_models import empty_voice
    write_json_atomic(root/'voice.json',empty_voice(manga_project.id))
    service=StoryAIService(directory,manga_project.id)
    config=dict(genre='青春',theme='言えなかった約束',mood='切なく温かな余韻',page_count=8,concept_count=3,max_cycles=5,goal_score=100)
    service.configure(config,expected_revision=0)
    with service.store.source_lock():pass
    return service


def job(service,kind,provider='mock',**kw):
    estimate=service.estimate(kind,provider=provider,spend_cap=kw.get('spend_cap','500'),new_request=kw.get('new_request',False))
    return service.create_job(kind,provider=provider,expected_revision=service.store.load()['revision'],approval=estimate['approval_key'] if provider!='mock' else None,**kw)


def concepts(service,provider='mock'):
    created=job(service,'concepts',provider);service.run_job(created['id'])
    data=service.store.load();idea=data['concept_batches'][-1]['ideas'][0]
    service.select_concept(idea['id'],expected_revision=data['revision'])
    return idea


def fake_card():
    card=template();card.update(model='test-story-model',returned_model_ids=['test-story-model'],verified_on=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat(),
        model_url='https://developers.openai.com/api/docs/models/test-only',input_per_million_usd='10',output_per_million_usd='20',
        max_input_tokens=10000,max_output_tokens=4096,fx_jpy_per_usd='160',tax_multiplier='1.10',tax_note='TEST ONLY: invented rates/model, not public pricing')
    card.update({k:True for k in FLAGS});return card


def paid(service,monkeypatch,*,usage=None,fail=False,mutate=None):
    service.configure(service.store.load()['settings'],fake_card(),expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1');calls=[]
    def send(body,key):
        calls.append(body)
        if fail:raise TimeoutError('Bearer dummy-sensitive-key')
        request=service.store.load()['requests'][-1];response=MockStoryAI().generate(request)
        data=dict(id='resp_test',status='completed',model='test-story-model',usage=usage if usage is not None else dict(input_tokens=30,output_tokens=40),
            output=[dict(type='message',content=[dict(type='output_text',text=response['text'])])])
        return mutate(data,request) if mutate else data
    service.adapter=ResponsesStoryAI(send=send,key='test-only-key')
    return calls
