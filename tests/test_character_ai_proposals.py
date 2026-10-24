import copy
import json
from uuid import uuid4

import pytest

from manga.artwork_models import CHARACTER_ALL_FIELDS
from manga.character_ai_extraction import extract, matches
from manga.storage import StorageError
from character_ai_fixtures import character_workspace, generate, adopt, bible, hashes


def test_extract_three_people_narrator_provenance_and_no_adoption(character_workspace):
    project, service = character_workspace
    before = service.artwork.path.read_bytes()
    result = generate(character_workspace)
    extraction = result['request']['character_ai']['extraction']
    assert len(extraction['candidates']) == 3 and len(extraction['narrator']['sources']) == 8
    assert not extraction['unknown_speakers'] and service.artwork.path.read_bytes() == before
    first = result['proposal']['payload']['items'][0]
    assert first['existing_id'] is None and first['fields']['age'] == first['fields']['gender'] == ''
    assert first['provenance']['hair_color']['status'] == 'ストーリー明記'
    assert first['provenance']['visual_identity']['status'] == 'AI提案'
    assert extraction['candidates'][0]['sources'][0]['page'] == 1
    assert result['request']['character_ai']['paid_api_calls'] == 0
    assert service.proposals.load()['proposals'][0]['decisions']['character-1'] == '確認待ち'


@pytest.mark.parametrize('name', ['悠 真 さん', '悠真くん', '佐藤 悠真', '悠真'])
def test_names_normalize_to_candidates_never_identify(character_workspace, name):
    _, service = character_workspace
    candidates = matches(name, service.artwork.load()['characters'])
    assert len(candidates) == 1 and candidates[0]['character_id'] == service.artwork.load()['characters'][0]['id']


def test_unicode_and_alias_matching(character_workspace):
    _, service = character_workspace
    people = copy.deepcopy(service.artwork.load()['characters'])
    people[0]['name'] = 'まなざし'
    assert matches('まなさ\u3099し', people)
    assert matches('ユウ', people, {people[0]['id']: ['ユウ']})
    story = copy.deepcopy(service.proposals.latest()['story'])
    for page in story['pages']:
        for panel in page['panels']: panel.update(description='ユウが窓を見る。', dialogue='', narration='ユウの最後の約束。')
    result = extract(story, dict(characters=people), aliases={people[0]['id']: ['ユウ']})
    assert len(result['candidates']) == 1 and result['candidates'][0]['matches'][0]['character_id'] == people[0]['id']


def test_same_name_people_require_explicit_identity(character_workspace):
    _, service = character_workspace
    service.artwork.save_character(dict(name='悠真'), expected_revision=service.artwork.load()['revision'])
    result = generate(character_workspace)
    assert len(result['request']['character_ai']['extraction']['candidates'][0]['matches']) == 2
    before = service.artwork.path.read_bytes()
    with pytest.raises(ValueError, match='重複候補'): adopt(character_workspace, result)
    assert service.artwork.path.read_bytes() == before
    chosen = service.artwork.load()['characters'][1]['id']
    change = adopt(character_workspace, result, existing_id=chosen, fields=['visual_identity'])
    assert change['result_id'] == chosen


def test_partial_update_preserves_uuid_primary_images_and_other_data(character_workspace):
    _, service = character_workspace
    result = generate(character_workspace)
    person = copy.deepcopy(service.artwork.load()['characters'][0])
    old = hashes(service.store.root)
    change = adopt(character_workspace, result, existing_id=person['id'], fields=['visual_identity'])
    current = service.artwork.load()['characters'][0]
    assert change['result_id'] == person['id'] and current['name'] == person['name']
    assert current['references'] == person['references'] and current['primary_reference_id'] == person['primary_reference_id']
    assert current['personality'] == person['personality']
    new = hashes(service.store.root)
    assert all(new[k] == v for k,v in old.items() if k not in ('artwork.json', 'assistant.json', 'assistant_pending.json'))
    with pytest.raises(ValueError, match='二重登録'): adopt(character_workspace, result, existing_id=person['id'])
    assert len(service.artwork.load()['characters']) == 1


def test_new_person_requires_approval_and_old_generic_adoption_cannot_bypass(character_workspace):
    _, service = character_workspace
    result = generate(character_workspace)
    change = adopt(character_workspace, result, 1)
    assert service.artwork._character(service.artwork.load(), change['result_id'])['name'] == '美咲'
    with pytest.raises(ValueError, match='同定'):
        from manga.assistant_storage import AssistantStore
        AssistantStore(service.store.root.parent, service.project_id).adopt(result['proposal']['id'], ['character-3'], expected_revision=service.proposals.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'], expected_editorial_revision=service.proposals.editorial.load()['revision'])


def test_request_deduplication_and_explicit_new_history(character_workspace):
    first = generate(character_workspace); second = generate(character_workspace)
    assert first['request']['id'] == second['request']['id']
    assert generate(character_workspace, new_request=True)['request']['id'] != first['request']['id']


def test_formal_finalized_version_is_required(character_workspace):
    project, service = character_workspace
    version = service.proposals.latest()
    with pytest.raises(StorageError): service.proposals.create_mock(project, str(uuid4()), expected_revision=0)
    service.stories.save(version['story'], expected_version_id=version['id'])
    with pytest.raises(ValueError, match='確定'): generate(character_workspace)
    assert not service.proposals.path.exists()


def test_zero_ambiguous_and_unproven_names_are_safe(character_workspace):
    _, service = character_workspace
    story = copy.deepcopy(service.proposals.latest()['story'])
    for page in story['pages']:
        for panel in page['panels']: panel.update(description='夕暮れ。', dialogue='私：「言えなかった」', narration='あと7日。')
    result = extract(story, dict(characters=[]))
    assert not result['candidates'] and len(result['unknown_speakers']) == 24
    with pytest.raises(ValueError, match='根拠'): extract(story, dict(characters=[]), additional_names=['新人物'])


def test_zero_candidates_persist_request_without_invalid_empty_proposal(character_workspace):
    project, service = character_workspace
    old = service.proposals.latest(); story = copy.deepcopy(old['story'])
    for page in story['pages']:
        for panel in page['panels']: panel.update(description='夕暮れの空。', dialogue='', narration='あと7日。')
    version = service.stories.save(story, expected_version_id=old['id'])
    service.proposals.editorial.finalize(expected_version_id=version['id'], expected_revision=service.proposals.editorial.load()['revision'])
    result = generate(character_workspace)
    assert result['proposal'] is None and not result['request']['character_ai']['extraction']['candidates']
    assert len(service.proposals.load()['requests']) == 1 and not service.proposals.load()['proposals']


def test_excessive_story_text_is_rejected(character_workspace):
    _, service = character_workspace
    story = copy.deepcopy(service.proposals.latest()['story'])
    story['pages'] *= 100
    for page in story['pages']:
        for panel in page['panels']: panel['description'] = '長'*4000
    with pytest.raises(ValueError, match='長すぎ'): extract(story, service.artwork.load())


@pytest.mark.parametrize('malformation', ['many', 'long', 'missing', 'duplicate'])
def test_malformed_inputs_do_not_overwrite_artwork(character_workspace, malformation):
    _, service = character_workspace
    result = generate(character_workspace); before = service.artwork.path.read_bytes()
    if malformation in ('many', 'long'):
        with pytest.raises(ValueError): generate(character_workspace, additional_names=['a']*51 if malformation == 'many' else ['x'*101])
    else:
        request = result['request']; payload = copy.deepcopy(result['proposal']['payload'])
        if malformation == 'missing': payload['items'][0]['fields'].pop('hair_color')
        else: payload['items'][1]['id'] = payload['items'][0]['id']
        with pytest.raises(ValueError): service.proposals.import_proposal(request['id'], json.dumps(payload), expected_revision=service.proposals.load()['revision'])
    assert service.artwork.path.read_bytes() == before
