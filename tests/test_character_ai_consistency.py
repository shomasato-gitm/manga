import copy
import hashlib
import json
from uuid import uuid4

import pytest

from manga.character_ai_assets import reference_path
from manga.character_ai_service import bible_stale
from manga.storage import StorageError
from character_ai_fixtures import character_workspace, generate, adopt, bible, hashes


def test_bible_keeps_reference_identity_hashes_and_profile(character_workspace):
    _, service = character_workspace
    old = service.artwork.path.read_bytes()
    row = bible(character_workspace)
    person = service.artwork.load()['characters'][0]
    assert row['character_id'] == person['id'] and row['primary_reference_id'] == person['primary_reference_id']
    assert row['references'][0]['sha256'] == hashlib.sha256(service.artwork.image_bytes(person['references'][0])).hexdigest()
    assert service.artwork.path.read_bytes() == old
    assert not bible_stale(row, service.proposals.latest(), person, service.snapshot(hashed=True)['references'][person['id']]['images'])


def test_locked_field_update_requires_explicit_unlock_then_marks_old_bible_stale(character_workspace):
    _, service = character_workspace
    old = bible(character_workspace)
    result = generate(character_workspace)
    person = service.artwork.load()['characters'][0]
    values = dict(result['proposal']['payload']['items'][0]['fields'], hair_color='金')
    with pytest.raises(ValueError, match='ロック'): adopt(character_workspace, result, existing_id=person['id'], fields=['hair_color'], values=values)
    bible(character_workspace, locked_fields=['clothing'])
    adopt(character_workspace, result, existing_id=person['id'], fields=['hair_color'], values=values)
    snapshot = service.snapshot(hashed=True)
    assert bible_stale(old, snapshot['latest'], snapshot['artwork']['characters'][0], snapshot['references'][person['id']]['images'])
    assert len(service.store.load()['bibles']) == 2


def direction(service, panel, composition):
    person = service.artwork.load()['characters'][0]
    service.artwork.save_direction(1, panel, dict(character_ids=[person['id']], composition=composition), story_version_id=service.proposals.latest()['id'], expected_revision=service.artwork.load()['revision'])


def test_fixed_conflicts_page_continuity_and_blocked_prompt(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    direction(service, 2, '悠真：髪色=金、服装=私服')
    result = service.run_checks(expected_revision=service.store.load()['revision'])
    conflicts = [i for i in result['issues'] if i['kind'] == 'fixed_conflict']
    assert {i['field'] for i in conflicts} == {'hair_color', 'clothing'}
    assert all(i['page'] == 1 and i['panel'] == 2 and i['evidence'] for i in conflicts)
    assert any(i['kind'] == 'page_continuity' for i in result['issues'])
    with pytest.raises(ValueError, match='矛盾'): service.prompt('panel', page=1, panel=2)


def test_approved_scene_exception_and_return_to_base_are_not_conflicts(character_workspace):
    _, service = character_workspace
    exception = dict(targets=[dict(page=1, panel=2)], changes=dict(clothing='体操服'), reason='体育の回想場面')
    bible(character_workspace, exceptions=[exception])
    direction(service, 2, '悠真：髪色=黒、服装=体操服')
    direction(service, 3, '悠真：髪色=黒、服装=制服')
    result = service.run_checks(expected_revision=service.store.load()['revision'])
    assert not [i for i in result['issues'] if i['kind'] in ('fixed_conflict', 'page_continuity')]
    prompt = service.prompt('panel', page=1, panel=2)
    assert '体操服' in prompt['prompt'] and '体育の回想' in prompt['prompt'] and '手動添付' in prompt['prompt']
    assert service.artwork.load()['characters'][0]['clothing'] == '制服'


@pytest.mark.parametrize('bad', ['duplicate', 'out_of_range', 'unknown_field', 'empty_reason'])
def test_invalid_scene_exceptions_do_not_overwrite(character_workspace, bad):
    _, service = character_workspace
    bible(character_workspace)
    old = service.store.path.read_bytes()
    exception = dict(targets=[dict(page=1, panel=2)], changes=dict(clothing='私服'), reason='回想')
    exceptions = [exception]
    if bad == 'duplicate': exceptions.append(dict(exception, changes=dict(clothing='制服')))
    elif bad == 'out_of_range': exception['targets'][0]['page'] = 9
    elif bad == 'unknown_field': exception['changes'] = dict(character_id=str(uuid4()))
    else: exception['reason'] = ''
    with pytest.raises(ValueError): bible(character_workspace, exceptions=exceptions)
    assert service.store.path.read_bytes() == old


def test_manual_profile_and_story_change_mark_checks_stale_without_deleting_history(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    check = service.run_checks(expected_revision=service.store.load()['revision'])
    person = service.artwork.load()['characters'][0]
    service.artwork.save_character(dict(person, hair_color='銀'), character_id=person['id'], expected_revision=service.artwork.load()['revision'])
    assert service.snapshot()['token'] != check['source_token']
    second = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'bible_stale' for i in second['issues'])
    old_version = service.proposals.latest()
    service.stories.save(old_version['story'], expected_version_id=old_version['id'])
    third = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'direction_stale' for i in third['issues']) and len(service.store.load()['checks']) == 3
    with pytest.raises(ValueError, match='確定'): bible(character_workspace)


def test_missing_reference_and_modified_bytes_are_detected(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    person = service.artwork.load()['characters'][0]
    ref = person['references'][0]; path = reference_path(service.store.root, ref)
    path.write_bytes(path.read_bytes()+b'changed')
    first = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'bible_stale' for i in first['issues'])
    path.unlink()  # fixture only: simulate missing local file
    second = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'reference_missing' and i['reference_id'] == ref['id'] for i in second['issues'])
    assert service.artwork.load()['characters'][0]['references'][0]['id'] == ref['id']


def test_panel_observations_and_old_version_are_preserved(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    person = service.artwork.load()['characters'][0]
    service.save_observation(1, 1, person['id'], dict(hair_color='白'), '指定欄に白と記載', version_id=service.proposals.latest()['id'], expected_revision=service.store.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'])
    result = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'fixed_conflict' and i['observed'] == '白' for i in result['issues'])
    old = service.proposals.latest(); service.stories.save(old['story'], expected_version_id=old['id'])
    result = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'observation_stale' for i in result['issues'])
    assert len(service.store.load()['observations']) == 1


def test_unregistered_references_remain_incomplete_and_prompt_history_is_explicit(character_workspace):
    _, service = character_workspace
    result = generate(character_workspace)
    change = adopt(character_workspace, result, 1)
    bible(character_workspace, change['result_id'], locked_fields=['visual_identity'])
    check = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'reference_unregistered' and i['character_id'] == change['result_id'] for i in check['issues'])
    before = hashes(service.store.root)
    package = service.prompt('character', character_id=change['result_id'], view='全身')
    assert package['warnings'] and '文字' in package['prompt'] and '全身' in package['prompt']
    assert before == hashes(service.store.root)
    service.save_prompt(package, expected_revision=service.store.load()['revision'])
    assert service.store.load()['prompts'][0]['story_version_id'] == service.proposals.latest()['id']
    service.artwork.save_style('フルカラー', expected_revision=service.artwork.load()['revision'])
    with pytest.raises(StorageError, match='作成し直'): service.save_prompt(package, expected_revision=service.store.load()['revision'])


def test_unknown_id_and_ambiguous_name_are_static_findings(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    snapshot = service.snapshot(hashed=True)
    snapshot['artwork']['panels']['1:1']['direction']['character_ids'] = [str(uuid4())]
    from manga.character_ai_checks import inspect
    assert any(i['kind'] == 'unknown_character_id' for i in inspect(snapshot))
    other = service.artwork.save_character(dict(name='悠真'), expected_revision=service.artwork.load()['revision'])
    direction(service, 1, '悠真：髪色=黒')
    check = service.run_checks(expected_revision=service.store.load()['revision'])
    assert any(i['kind'] == 'feature_identity_ambiguous' for i in check['issues'])


def test_full_color_and_all_character_prompt_views(character_workspace):
    _, service = character_workspace
    row = bible(character_workspace)
    person = service.artwork.load()['characters'][0]
    service.artwork.save_style('フルカラーの日本漫画', expected_revision=service.artwork.load()['revision'])
    for view in ('正面', '斜め', '全身', '表情差分'):
        package = service.prompt('character', character_id=person['id'], view=view, expression='笑顔')
        assert view in package['prompt'] and 'フルカラー' in package['prompt']
        assert row['id'] in package['bible_ids'] and person['primary_reference_id'] in package['prompt']
    package = service.prompt('panel', page=1, panel=1)
    assert '想定アスペクト比' in package['prompt'] and '吹き出し' in package['prompt']
