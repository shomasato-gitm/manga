import copy
from uuid import uuid4
import pytest

from manga.budget.config import open_budget
from manga.budget.guard import claim_provider_call,claim_transport
from manga.voice_models import digest
from manga.story_ai_provider import ResponsesStoryAI,transport
from manga.voice_polly import PollyVoice
from manga.voice_service import VoiceService
from text_guard_fakes import guarded
from story_ai_fixtures import ai_workspace,paid,job,concepts
from voice_fixtures import voice_workspace,paid_setup,all_ids
from test_story_ai_gemini import setup,next_step,card


def test_raw_transports_and_adapters_cannot_run_outside_budget(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch);created=job(service,'concepts','openai-responses')
    from manga.story_ai_requests import next_request
    request=next_request(service,created['id'])
    with pytest.raises(ValueError):service.adapter.generate(request)
    with pytest.raises(ValueError):transport({'model':'test-story-model'},'fake')
    voice=PollyVoice(send=lambda _:pytest.fail('must not send'),keys={'AWS_ACCESS_KEY_ID':'fake','AWS_SECRET_ACCESS_KEY':'fake'})
    with pytest.raises(ValueError):voice.synthesize('<speak>確認</speak>',dict(engine='neural',voice_id='Takumi'))
    assert not calls


def test_transport_claim_checks_payload_and_is_once(tmp_path):
    checksum=digest({'a':1})
    def operation():
        claim_provider_call('openai-responses','test-story-model',payload_sha256=checksum)
        with pytest.raises(ValueError):claim_transport('openai-responses','test-story-model','different')
        claim_transport('openai-responses','test-story-model',checksum)
        with pytest.raises(ValueError):claim_transport('openai-responses','test-story-model',checksum)
        with pytest.raises(ValueError):claim_provider_call('openai-responses','test-story-model')
    guarded(tmp_path,operation)


def test_next_approval_stale_and_stale_character_blocks_adoption(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup(service,monkeypatch);concepts(service,'gemini-text')
    created=job(service,'story','gemini-text');service.run_job(created['id'])
    estimate=service.next_estimate(created['id'])
    service.approve_next(created['id'],approval=estimate['approval_key'],expected_revision=service.store.load()['revision'])
    service.run_job(created['id'])
    with pytest.raises(ValueError):service.approve_next(created['id'],approval=estimate['approval_key'],expected_revision=service.store.load()['revision'])
    candidate=service.store.load()['candidates'][0];character=service.artwork.load()['characters'][0]
    service.artwork.save_character(dict(character,name=character['name'],hair_color='金'),character_id=character['id'],expected_revision=service.artwork.load()['revision'])
    from manga.storage import StorageError
    with pytest.raises(StorageError):service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_story_id=service.stories.load_history()[-1]['id'],confirmed=True)
    assert not service.store.load()['adoptions']


def test_uncertain_global_budget_and_project_limit_block_gemini(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup(service,monkeypatch);ledger=open_budget(service.directory)
    identifier=str(uuid4());ledger.reserve(project_id=service.project_id,request_id=identifier,provider='other',model='test',kind='test',max_jpy='500')
    with pytest.raises(ValueError):job(service,'concepts','gemini-text')
    assert not calls


def test_expired_free_confirmation_never_falls_back(ai_workspace,monkeypatch):
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    service=ai_workspace;calls=setup(service,monkeypatch);value=card();value.update(free_tier_reviewed=True,
        free_tier_verified_on=(datetime.now(ZoneInfo('Asia/Tokyo')).date()-timedelta(days=3)).isoformat())
    service.configure(service.store.load()['settings'],value,expected_revision=service.store.load()['revision'])
    with pytest.raises(ValueError,match='無料枠'):service.estimate('concepts',provider='gemini-text',mode='verified-free')
    assert not calls and not service.store.load()['jobs']


def test_polly_multi_send_requires_individual_approvals(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)
    with pytest.raises(ValueError,match='1発話'):service.generate(ids,approval=service.estimate(ids)['approval_key'])
    assert not calls
    ids=ids[:1];approval=service.estimate(ids,regenerate=True)['approval_key']
    service.generate(ids,regenerate=True,approval=approval)
    with pytest.raises(ValueError,match='承認'):service.generate(ids,regenerate=True,approval=approval)
    assert len(calls)==1
