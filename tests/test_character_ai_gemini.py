import copy
import json
from uuid import uuid4

import pytest

from manga.artwork_models import CHARACTER_ALL_FIELDS
from manga.budget.config import open_budget
from manga.character_ai_gemini import GeminiCharacterAI,checked_output,payload
from manga.character_ai_service import CharacterAIService
from manga.character_ai_extraction import feature_matches
from manga.storage import StorageError
from manga.story_ai_gemini import GeminiTransportError
from character_gemini_fixtures import character_gemini,generate,fake_adapter,answer,adopt,SECRET


def test_two_people_24_panels_generate_confirm_partial_adopt_and_bible(character_gemini):
    project,service=character_gemini;before=service.artwork.path.read_bytes();old=copy.deepcopy(service.artwork.load()['characters'][0])
    adapter,calls=fake_adapter(service);result=generate(character_gemini,adapter=adapter)
    assert len(calls)==1 and result['request']['character_ai']['state']=='saved'
    extraction=result['request']['character_ai']['extraction']
    assert {c['name'] for c in extraction['candidates']}=={'咲','春斗'}
    assert len({(s['page'],s['panel']) for c in extraction['candidates'] for s in c['sources']})==24
    assert all(i['existing_id'] is None for i in result['proposal']['payload']['items'])
    assert set(result['proposal']['decisions'].values())=={'確認待ち'} and service.artwork.path.read_bytes()==before
    first=result['proposal']['payload']['items'][0]
    assert first['fields']['age']=='17歳' and first['fields']['gender']=='女性'
    assert result['proposal']['payload']['items'][1]['fields']['age']==''
    assert first['provenance']['relationships']['status']=='AI提案'
    assert extraction['candidates'][0]['matches'][0]['character_id']==old['id']
    values=dict(first['fields'],personality='ユーザーが修正した性格')
    change=adopt(character_gemini,result,existing_id=old['id'],fields=['personality','relationships'],values=values)
    person=service.artwork._character(service.artwork.load(),old['id'])
    assert change['result_id']==old['id'] and person['personality']==values['personality'] and person['hair_color']==old['hair_color']
    assert person['references']==old['references'] and person['primary_reference_id']==old['primary_reference_id']
    adopted=service.proposals.load()['proposals'][0]['payload']['items'][0]
    assert adopted['provenance']['personality']['status']=='ユーザー編集'
    other=adopt(character_gemini,result,1);assert len(service.artwork.load()['characters'])==2
    bible=service.save_bible(other['result_id'],version_id=service.proposals.latest()['id'],expected_revision=service.store.load()['revision'],
        expected_artwork_revision=service.artwork.load()['revision'],locked_fields=['visual_identity'],aliases=[],exceptions=[],reason='人間が提案・事実・関係を確認',confirmed=True)
    assert bible['profile']['relationships'] and bible['source_adoption_ids']
    assert service.proposals.latest()['story']['pages'][-1]['page']==8
    restarted=CharacterAIService(service.store.root.parent,project.id)
    assert restarted.store.load()['bibles'][-1]['id']==bible['id']
    assert restarted.recover_gemini(result['request']['id'])['proposal']['id']==result['proposal']['id'] and len(calls)==1
    assert (service.store.root/'voice'/'existing.bin').read_bytes()==b'preserve existing voice'
    assert (service.store.root/'video'/'existing.bin').read_bytes()==b'preserve existing video'


@pytest.mark.parametrize('fault',['json','extra','id','missing_person','duplicate_person','existing_id','name','false_fact','age','gender','unknown_status','empty_fact','wrong_source'])
def test_invalid_json_or_unfounded_attributes_never_become_formal(character_gemini,fault):
    _,service=character_gemini;before=service.artwork.path.read_bytes()
    def mutate(value,request):
        item=value['items'][0]
        if fault=='json':return '{not JSON'
        if fault=='extra':value['auto_adopt']=True
        if fault=='id':value['request_id']=str(uuid4())
        if fault=='missing_person':value['items'].pop()
        if fault=='duplicate_person':value['items'][1]=copy.deepcopy(item)
        if fault=='existing_id':item['existing_id']=service.artwork.load()['characters'][0]['id']
        if fault=='name':item['fields']['name']='別人'
        if fault=='false_fact':item['provenance']['personality']=dict(status='ストーリー明記',evidence=item['provenance']['name']['evidence'])
        if fault in ('age','gender'):
            other=value['items'][1];other['fields'][fault]='25歳' if fault=='age' else '男性';other['provenance'][fault]=dict(status='AI提案',evidence='推測')
        if fault=='unknown_status':item['provenance']['personality']['status']='確認済み'
        if fault=='empty_fact':item['fields']['height']='';item['provenance']['height']=dict(status='ストーリー明記',evidence='')
        if fault=='wrong_source':item['provenance']['hair_color']['evidence']='脚本にない引用'
        return value
    adapter,calls=fake_adapter(service,mutate=mutate);result=generate(character_gemini,adapter=adapter)
    assert len(calls)==1 and result['request']['character_ai']['state']=='invalid' and result['proposal'] is None
    assert service.artwork.path.read_bytes()==before and not service.proposals.load()['adoptions']
    assert open_budget(service.store.root.parent).request_status(result['request']['id'])['state']=='settled'


def test_ambiguous_speaker_is_not_assigned_to_named_people(character_gemini):
    project,service=character_gemini;old=service.proposals.latest();story=copy.deepcopy(old['story']);story['pages'][0]['panels'][2]['dialogue']='私：「待って」'
    version=service.stories.save(story,expected_version_id=old['id']);service.proposals.editorial.finalize(expected_version_id=version['id'],expected_revision=service.proposals.editorial.load()['revision'])
    adapter,calls=fake_adapter(service);result=generate(character_gemini,adapter=adapter)
    assert len(result['request']['character_ai']['extraction']['unknown_speakers'])==1
    assert len(result['proposal']['payload']['items'])==2 and len(calls)==1


def test_only_unknown_speakers_stop_before_request_or_budget(character_gemini):
    project,service=character_gemini;old=service.proposals.latest();story=copy.deepcopy(old['story'])
    for page in story['pages']:
        for panel in page['panels']:panel.update(description='桜の風景。',dialogue='私：「待って」',narration='')
    # 既存人物名も本文に存在しない。
    version=service.stories.save(story,expected_version_id=old['id']);service.proposals.editorial.finalize(expected_version_id=version['id'],expected_revision=service.proposals.editorial.load()['revision'])
    with pytest.raises(ValueError,match='候補'):service.preview_gemini(project,version['id'])
    assert not service.proposals.load()['requests']


@pytest.mark.parametrize('failure',[TimeoutError('DO NOT LOG '+SECRET),GeminiTransportError('bad_request_api_key'),GeminiTransportError('rate_limited')])
def test_api_failure_keeps_reservation_and_blocks_other_paid_paths(character_gemini,failure,capsys):
    project,service=character_gemini;adapter,calls=fake_adapter(service,fail=failure)
    result=generate(character_gemini,adapter=adapter);identifier=result['request']['id']
    assert result['request']['character_ai']['state']=='uncertain' and len(calls)==1
    ledger=open_budget(service.store.root.parent);assert ledger.request_status(identifier)['state']=='uncertain' and ledger.summary(project.id)['blocked']
    with pytest.raises(ValueError):generate(character_gemini,adapter=adapter,new_request=True)
    with pytest.raises(ValueError,match='未確定'):service.recover_gemini(identifier)
    for path in service.store.root.parent.parent.rglob('*'):
        if path.is_file():assert SECRET.encode() not in path.read_bytes()
    out=capsys.readouterr();assert SECRET not in out.out+out.err


@pytest.mark.parametrize('block',['project','monthly','uncertain'])
def test_shared_budget_blocks_without_creating_request(character_gemini,block):
    project,service=character_gemini;ledger=open_budget(service.store.root.parent)
    for _ in range(10 if block=='monthly' else 1):
        identifier=str(uuid4());ledger.reserve(project_id=str(uuid4()) if block=='monthly' else project.id,request_id=identifier,provider='gemini-text',model='gemini-2.5-flash-lite',kind='existing-story',max_jpy='500' if block!='uncertain' else '23.8128')
        if block=='uncertain':ledger.mark_uncertain(identifier)
    before=ledger.pending_requests();adapter,calls=fake_adapter(service)
    with pytest.raises(ValueError,match='予算|未確定|残額|確認待ち'):generate(character_gemini,adapter=adapter)
    assert not calls and not service.proposals.load()['requests'] and ledger.pending_requests()==before


def test_approval_is_per_input_and_old_approval_cannot_send_twice(character_gemini):
    project,service=character_gemini;version=service.proposals.latest()['id'];plan=service.preview_gemini(project,version);adapter,calls=fake_adapter(service)
    for approval in (None,'wrong'):
        with pytest.raises(ValueError,match='承認'):service.generate_gemini(project,version,approval=approval,expected_revision=plan['revision'],adapter=adapter)
    result=service.generate_gemini(project,version,approval=plan['approval_key'],expected_revision=plan['revision'],adapter=adapter)
    with pytest.raises((ValueError,StorageError)):service.generate_gemini(project,version,approval=plan['approval_key'],expected_revision=plan['revision'],adapter=adapter)
    assert len(calls)==1
    assert generate(character_gemini,adapter=adapter)['request']['id']==result['request']['id'] and len(calls)==1
    assert generate(character_gemini,adapter=adapter,new_request=True)['request']['id']!=result['request']['id'] and len(calls)==2


@pytest.mark.parametrize('case',['key','flag','free_tier','wrong_model','unfinalized'])
def test_missing_conditions_stop_before_budget_or_artwork_mutation(character_gemini,monkeypatch,case):
    project,service=character_gemini;before=service.artwork.path.read_bytes();adapter,calls=fake_adapter(service)
    if case=='key':monkeypatch.delenv('GEMINI_API_KEY')
    if case=='flag':monkeypatch.delenv('MANGA_ENABLE_TEXT_API')
    if case in ('free_tier','wrong_model'):
        from manga.story_ai_storage import AIStoryStore
        from manga.story_ai_service import StoryAIService
        from test_story_ai_gemini import card
        text=StoryAIService(service.store.root.parent,project.id);data=text.store.load();value=copy.deepcopy(data['card']);value['free_tier_reviewed']=False
        if case=='wrong_model':value=card()
        text.configure(data['settings'],value,expected_revision=data['revision'])
    if case=='unfinalized':service.stories.save(service.proposals.latest()['story'],expected_version_id=service.proposals.latest()['id'])
    with pytest.raises(ValueError):generate(character_gemini,adapter=adapter)
    assert not calls and service.artwork.path.read_bytes()==before and not service.proposals.load()['requests']
