import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from manga.budget import BudgetLedger
from manga.budget.config import open_budget
from manga.character_ai_service import CharacterAIService
from manga.character_ai_gemini import GeminiCharacterAI
from manga.story_ai_storage import AIStoryStore
from manga.storage import StorageError
from character_gemini_fixtures import character_gemini,generate,fake_adapter,answer,adopt
from test_gemini35 import raw35


def test_request_save_failure_cannot_send_or_reserve(character_gemini,monkeypatch):
    import manga.assistant_repository as repository
    project,service=character_gemini;adapter,calls=fake_adapter(service);original=repository.write_json_atomic
    def fail(path,data):
        if path.name=='assistant.json':raise OSError('fake disk failure')
        return original(path,data)
    monkeypatch.setattr(repository,'write_json_atomic',fail)
    with pytest.raises((OSError,StorageError)):generate(character_gemini,adapter=adapter)
    assert not calls and not service.proposals.load()['requests'] and not open_budget(service.store.root.parent).pending_requests()


def test_receipt_save_failure_preserves_uncertain_until_human_billing_review(character_gemini,monkeypatch):
    project,service=character_gemini;adapter,calls=fake_adapter(service);original=AIStoryStore.write
    def fail(self,path,content):
        if 'character_responses/' in path and path.endswith('.json'):raise OSError('fake disk full')
        return original(self,path,content)
    monkeypatch.setattr(AIStoryStore,'write',fail);result=generate(character_gemini,adapter=adapter);identifier=result['request']['id']
    assert result['request']['character_ai']['state']=='uncertain'
    ledger=open_budget(service.store.root.parent)
    with pytest.raises(ValueError,match='未確定'):service.recover_gemini(identifier)
    # 人間の無課金照合をテスト内で明示。実装からの自動精算ではない。
    ledger.reconcile_no_charge(identifier,confirmed_no_charge=True)
    monkeypatch.setattr(AIStoryStore,'write',original)
    recovered=service.recover_gemini(identifier)
    assert recovered['request']['character_ai']['state']=='invalid' and recovered['proposal'] is None and len(calls)==1
    assert generate(character_gemini,adapter=adapter,new_request=True)['request']['character_ai']['state']=='saved'
    assert len(calls)==2


def test_proposal_save_failure_recovers_from_receipt_after_restart_once(character_gemini,monkeypatch):
    import manga.assistant_repository as repository
    project,service=character_gemini;adapter,calls=fake_adapter(service);original=repository.write_json_atomic;failed=[]
    def fail(path,data):
        if path.name=='assistant.json' and data['proposals'] and not failed:
            failed.append(True);raise OSError('fake interruption while saving proposal')
        return original(path,data)
    monkeypatch.setattr(repository,'write_json_atomic',fail)
    with pytest.raises((OSError,StorageError)):generate(character_gemini,adapter=adapter)
    request=service.proposals.load()['requests'][0]
    assert request['character_ai']['state']=='sending' and len(calls)==1
    assert open_budget(service.store.root.parent).request_status(request['id'])['state']=='settled'
    monkeypatch.setattr(repository,'write_json_atomic',original)
    restarted=CharacterAIService(service.store.root.parent,project.id)
    first=restarted.recover_gemini(request['id']);second=restarted.recover_gemini(request['id'])
    assert first['proposal']['id']==second['proposal']['id'] and len(restarted.proposals.load()['proposals'])==1 and len(calls)==1


def test_settlement_failure_requires_manual_review_then_reuses_saved_result(character_gemini,monkeypatch):
    project,service=character_gemini;adapter,calls=fake_adapter(service);original=BudgetLedger.settle
    monkeypatch.setattr(BudgetLedger,'settle',lambda *a,**kw:(_ for _ in ()).throw(OSError('fake settle failure')))
    result=generate(character_gemini,adapter=adapter);identifier=result['request']['id'];ledger=open_budget(service.store.root.parent)
    assert ledger.request_status(identifier)['state']=='uncertain' and result['proposal'] is None
    with pytest.raises(ValueError):service.recover_gemini(identifier)
    monkeypatch.setattr(BudgetLedger,'settle',original)
    ledger.reconcile_no_charge(identifier,confirmed_no_charge=True)
    result=service.recover_gemini(identifier)
    assert result['request']['character_ai']['state']=='saved' and len(calls)==1


@pytest.mark.parametrize('source',['story','person','bible'])
def test_changed_sources_cannot_publish_old_result(character_gemini,source):
    _,service=character_gemini;before=service.proposals.latest()
    def send(body,key):
        request=service.proposals.load()['requests'][-1];value=answer(request)
        person=service.artwork.load()['characters'][0]
        if source=='story':
            story=copy.deepcopy(before['story']);story['pages'][0]['panels'][0]['dialogue']='咲：「新しい約束」'
            version=service.stories.save(story,expected_version_id=before['id']);service.proposals.editorial.finalize(expected_version_id=version['id'],expected_revision=service.proposals.editorial.load()['revision'])
        elif source=='person':service.artwork.save_character(dict(person,personality='別画面で変更'),expected_revision=service.artwork.load()['revision'],character_id=person['id'])
        else:service.save_bible(person['id'],version_id=before['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],locked_fields=[],aliases=['サキ'],exceptions=[],reason='明示確認',confirmed=True)
        return raw35(json.dumps(value))
    result=generate(character_gemini,adapter=GeminiCharacterAI(send=send))
    assert result['request']['character_ai']['state']=='stale' and result['proposal'] is None
    assert open_budget(service.store.root.parent).request_status(result['request']['id'])['state']=='settled'


def test_concurrent_invocation_has_one_execution_right(character_gemini):
    project,service=character_gemini;started=Event();release=Event();calls=[]
    def send(body,key):
        calls.append(body);started.set();assert release.wait(10)
        return raw35(json.dumps(answer(service.proposals.load()['requests'][-1])))
    adapter=GeminiCharacterAI(send=send);version=service.proposals.latest()['id'];plan=service.preview_gemini(project,version)
    def run():return service.generate_gemini(project,version,approval=plan['approval_key'],expected_revision=plan['revision'],adapter=adapter)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(run)
        try:
            assert started.wait(10)
            with pytest.raises(StorageError,match='実行中'):run()
        finally:release.set()
        assert future.result()['request']['character_ai']['state']=='saved'
    assert len(calls)==1 and len(service.proposals.load()['proposals'])==1


@pytest.mark.parametrize('case',['hash','path','missing_db','missing_receipt'])
def test_history_ledger_and_receipt_tampering_fail_closed(character_gemini,monkeypatch,case,tmp_path):
    project,service=character_gemini;adapter,calls=fake_adapter(service);result=generate(character_gemini,adapter=adapter);identifier=result['request']['id']
    store=AIStoryStore(service.store.root.parent,project.id);path=store.safe(f'story_ai/character_responses/{identifier}.json');before=service.artwork.path.read_bytes()
    if case=='hash':path.write_bytes(path.read_bytes()+b' ')
    if case=='missing_receipt':path.unlink()
    if case=='path':monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'different.sqlite3'))
    if case=='missing_db':open_budget(service.store.root.parent).path.unlink()
    with pytest.raises((ValueError,OSError,StorageError)):service.recover_gemini(identifier)
    if case in ('hash','missing_receipt'):
        with pytest.raises((ValueError,OSError,StorageError)):adopt(character_gemini,result,existing_id=service.artwork.load()['characters'][0]['id'])
    else:
        with pytest.raises((ValueError,OSError,StorageError)):generate(character_gemini,adapter=adapter,new_request=True)
    assert service.artwork.path.read_bytes()==before and len(calls)==1
