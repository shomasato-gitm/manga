import copy
import io
import json

import pytest

from manga.artwork_models import CHARACTER_ALL_FIELDS
from manga.character_ai_extraction import feature_matches
from manga.character_ai_gemini import GeminiCharacterAI,checked_output,payload
from manga.story_ai_gemini import parse_response
from manga.storage import StorageError,write_json_atomic
from character_gemini_fixtures import character_gemini,generate,fake_adapter,answer,adopt,SECRET
from character_ai_fixtures import character_workspace,generate as mock_generate
from test_gemini35 import raw35


def test_shared_https_transport_and_model_bound_structure(character_gemini,monkeypatch,capsys):
    from manga import story_ai_gemini as module
    project,service=character_gemini;calls=[];original=service.artwork.load()['characters'][0]
    class Reply(io.BytesIO):
        status=200
        def read1(self,n):return self.read(n)
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'
            assert request.get_header('X-goog-api-key')==SECRET and timeout==30 and request.get_method()=='POST'
            body=json.loads(request.data);calls.append(body)
            assert SECRET.encode() not in request.data and original['references'][0]['path'] not in json.dumps(body)
            config=body['generationConfig'];assert config['thinkingConfig']=={'thinkingLevel':'minimal'} and config['candidateCount']==1
            assert config['maxOutputTokens']==16384 and config['responseFormat']['text']['mimeType']=='APPLICATION_JSON'
            rule=config['responseFormat']['text']['schema'];assert rule['additionalProperties'] is False
            assert rule['properties']['items']['items']['properties']['existing_id']=={'type':'null'}
            current=service.proposals.load()['requests'][-1]
            return Reply(json.dumps(raw35(json.dumps(answer(current)))).encode())
    monkeypatch.setattr(module,'build_opener',lambda *args:Opener())
    result=generate(character_gemini)
    assert result['request']['character_ai']['state']=='saved' and len(calls)==1
    for path in service.store.root.parent.parent.rglob('*'):
        if path.is_file():assert SECRET.encode() not in path.read_bytes()
    out=capsys.readouterr();assert SECRET not in out.out+out.err


def test_adapter_direct_invocation_has_no_execution_right(character_gemini):
    project,service=character_gemini;plan=service.preview_gemini(project,service.proposals.latest()['id']);adapter,calls=fake_adapter(service)
    with pytest.raises(ValueError,match='実行権'):adapter.generate(plan['request'])
    assert not calls


@pytest.mark.parametrize('fault',['model','usage','incomplete'])
def test_model_usage_and_completion_must_be_valid(character_gemini,fault):
    _,service=character_gemini
    def send(body,key):
        result=raw35(json.dumps(answer(service.proposals.load()['requests'][-1])))
        if fault=='model':result['modelVersion']='gemini-2.5-flash-lite'
        if fault=='usage':result['usageMetadata']={}
        if fault=='incomplete':result['candidates'][0]['finishReason']='MAX_TOKENS'
        return result
    result=generate(character_gemini,adapter=GeminiCharacterAI(send=send))
    assert result['proposal'] is None and result['request']['character_ai']['state']==('invalid' if fault=='incomplete' else 'uncertain')


def test_old_proposals_without_relationships_are_read_and_partial_update_preserves_absence(character_workspace):
    project,service=character_workspace;result=mock_generate(character_workspace);data=service.proposals.load()
    for proposal in data['proposals']:
        for field in ('payload','original_payload'):
            for item in proposal[field]['items']:
                item['fields'].pop('relationships',None);item['provenance'].pop('relationships',None)
    write_json_atomic(service.proposals.path,data)
    before=service.artwork.load()['characters'][0]
    assert 'relationships' not in before
    result['proposal']=service.proposals.load()['proposals'][0]
    from character_ai_fixtures import adopt as old_adopt
    old_adopt(character_workspace,result,existing_id=before['id'],fields=['visual_identity'])
    current=service.artwork.load()['characters'][0]
    assert 'relationships' not in current and current['references']==before['references']
    assert current['id']==before['id'] and service.proposals.load()['requests'][0]['character_ai']['provider']=='mock'


def test_feature_duplicates_are_only_review_candidates(character_gemini):
    _,service=character_gemini;person=service.artwork.load()['characters'][0]
    values=dict(name='別人',visual_identity='共通の小物')
    people=copy.deepcopy(service.artwork.load()['characters']);people[0]['visual_identity']=values['visual_identity']
    found=feature_matches(values,people)
    assert found[0]['character_id']==person['id'] and found[0]['fields']==['visual_identity']
    assert not feature_matches(values,people,exclude=person['id'])


def test_pre_phase10_pending_adoption_journal_can_resume(character_workspace,monkeypatch):
    import manga.assistant_repository as repository
    from character_ai_fixtures import adopt as old_adopt
    _,service=character_workspace;result=mock_generate(character_workspace);person=service.artwork.load()['characters'][0]
    original=repository.write_json_atomic
    def interrupt(path,data):
        if path.name=='assistant_pending.json' and data.get('status')=='done':raise OSError('old interrupted journal')
        return original(path,data)
    monkeypatch.setattr(repository,'write_json_atomic',interrupt)
    with pytest.raises((OSError,StorageError)):old_adopt(character_workspace,result,existing_id=person['id'],fields=['visual_identity'])
    monkeypatch.setattr(repository,'write_json_atomic',original)
    journal=json.loads(service.proposals.journal_path.read_text())
    # 旧バージョンの正規形式を再現する（実装によるデータ移行ではない）。
    for data in (journal['before_assistant'],journal['after_assistant']):
        for proposal in data['proposals']:
            for collection in ('payload','original_payload'):
                for item in proposal[collection]['items']:
                    item['fields'].pop('relationships',None);item['provenance'].pop('relationships',None)
        for adoption in data['adoptions']:
            for change in adoption['changes']:change['before'].pop('relationships',None)
    write_json_atomic(service.proposals.path,journal['after_assistant']);write_json_atomic(service.proposals.journal_path,journal)
    service.proposals.resume();service.proposals.resume()
    assert not service.proposals.pending() and service.artwork.load()['characters'][0]['id']==person['id']
    assert service.artwork.load()['characters'][0]['references']==person['references']


def test_existing_identity_and_locked_bible_require_human_approval(character_gemini):
    _,service=character_gemini;person=service.artwork.load()['characters'][0]
    service.save_bible(person['id'],version_id=service.proposals.latest()['id'],expected_revision=0,expected_artwork_revision=service.artwork.load()['revision'],locked_fields=['hair_color'],aliases=[],exceptions=[],reason='固定特徴確認',confirmed=True)
    adapter,_=fake_adapter(service);result=generate(character_gemini,adapter=adapter)
    with pytest.raises(ValueError,match='重複候補'):adopt(character_gemini,result)
    with pytest.raises(ValueError,match='ロック'):adopt(character_gemini,result,existing_id=person['id'],fields=['hair_color'])
    item=result['proposal']['payload']['items'][0]
    with pytest.raises(ValueError,match='同定'):service.proposals.adopt_fields(result['proposal']['id'],item['id'],item['fields'],['personality'],person['id'],expected_revision=service.proposals.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],expected_editorial_revision=service.proposals.editorial.load()['revision'],expected_character_revision=service.store.load()['revision'])
