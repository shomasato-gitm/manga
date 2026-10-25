"""独立した8ページ24コマの無料検証。人物一致・公開品質は保証しない。"""
import copy
import json
from uuid import uuid4

import pytest

from manga.direction_drafts import prepare_drafts, save_drafts, script_hints
from manga.storage import StorageError, write_json_atomic
from manga.story_storage import StoryStore
from manga.manual_image_steps import step_states
from image_ai_fixtures import image_workspace, hashes


def clear_directions(service):
    data = service.artwork.load()
    for entry in data['panels'].values():
        entry['direction'] = None
    write_json_atomic(service.artwork.path, data)


def test_batch_is_readonly_and_saves_only_explicitly_chosen_missing_targets(image_workspace):
    _, service, _ = image_workspace
    existing = copy.deepcopy(service.artwork.load()['panels']['1:1'])
    data = service.artwork.load()
    for key in ('1:2','2:3'):
        data['panels'][key]['direction'] = None
    write_json_atomic(service.artwork.path, data)
    before = hashes(service.store.root)
    batch = prepare_drafts(service.artwork)
    assert len(batch['items']) == 24 and hashes(service.store.root) == before
    with pytest.raises(ValueError,match='上書き'):
        save_drafts(service.artwork,batch,['1:1'],confirmed=True)
    with pytest.raises(ValueError,match='明示確認'):
        save_drafts(service.artwork,batch,['2:3'])
    assert hashes(service.store.root) == before
    assert save_drafts(service.artwork,batch,['2:3'],confirmed=True) == dict(saved=['2:3'],errors={})
    assert service.artwork.load()['panels']['1:1'] == existing
    assert service.artwork.load()['panels']['1:2']['direction'] is None
    assert service.artwork.load()['panels']['2:3']['direction']['story_version_id'] == batch['story_version_id']


def test_hints_leave_unknowns_blank_and_do_not_invent_cast(image_workspace):
    _, service, _ = image_workspace
    latest = copy.deepcopy(service.store.stories.load_history()[-1])
    panel = latest['story']['pages'][0]['panels'][0]
    panel.update(description='場所：駅前。時間帯：夕方。表情：笑顔。構図：引き。画像アスペクト比：16:9。',dialogue='悠真：待っていたよ',narration='')
    first = script_hints(latest, service.artwork.load())[0]
    assert first['fields']['location'] == '駅前' and first['fields']['time_of_day'] == '夕方'
    assert first['fields']['expression'] == '笑顔' and first['hints']['aspect_ratio'] == '16:9'
    assert first['fields']['pose'] == first['fields']['background'] == ''
    panel.update(description='夕暮れの駅前で彼女が微笑む。',dialogue='誰か：待って',narration='')
    row = script_hints(latest,service.artwork.load())[0]
    assert not row['fields']['location'] and not row['fields']['expression']
    assert not row['fields']['character_ids'] and row['issues']


@pytest.mark.parametrize('change',['script','revision','deleted','alias'])
def test_batch_refuses_stale_script_person_revision_and_alias_without_writes(image_workspace,change):
    _, service, chars = image_workspace
    clear_directions(service)
    batch = prepare_drafts(service.artwork)
    if change == 'script':
        latest = service.store.stories.load_history()[-1]
        service.store.stories.save(latest['story'],expected_version_id=latest['id'])
    elif change == 'revision':
        service.artwork.save_style('別の作風',expected_revision=service.artwork.load()['revision'])
    elif change == 'deleted':
        service.artwork.delete_character(service.artwork.load()['characters'][0]['id'],expected_revision=service.artwork.load()['revision'])
    else:
        from manga.image_ai_samples import confirm_bible
        data = chars.store.load()
        data['bibles'][-1]['aliases'] = ['別名']
        chars.store.save(data,'test_alias_change')
    before = hashes(service.store.root)
    with pytest.raises((ValueError,StorageError)):
        save_drafts(service.artwork,batch,['2:1'],confirmed=True)
    assert hashes(service.store.root) == before


def test_batch_refuses_unknown_uuid_before_any_save(image_workspace):
    _, service, _ = image_workspace
    clear_directions(service)
    batch = prepare_drafts(service.artwork)
    batch['items'][1]['fields']['character_ids'] = [str(uuid4())]
    before = hashes(service.store.root)
    with pytest.raises(ValueError,match='UUID'):
        save_drafts(service.artwork,batch,['1:1','1:2'],confirmed=True)
    assert hashes(service.store.root) == before


def test_batch_partial_failure_preserves_saved_targets_and_reprepares_remaining(image_workspace,monkeypatch):
    _, service, _ = image_workspace
    clear_directions(service)
    original = service.artwork.save_direction
    def fail_second(page,panel,*args,**kwargs):
        if panel == 2:
            raise StorageError('保存先の空き容量不足')
        return original(page,panel,*args,**kwargs)
    batch = prepare_drafts(service.artwork)
    monkeypatch.setattr(service.artwork,'save_direction',fail_second)
    result = save_drafts(service.artwork,batch,['2:1','2:2','2:3'],confirmed=True)
    assert result['saved'] == ['2:1'] and set(result['errors']) == {'2:2','2:3'}
    new = prepare_drafts(service.artwork)
    assert next(r for r in new['items'] if r['key']=='2:1')['existing']
    assert service.artwork.load()['panels']['2:2']['direction'] is None
    monkeypatch.setattr(service.artwork,'save_direction',original)
    assert save_drafts(service.artwork,new,['2:2','2:3'],confirmed=True)['saved'] == ['2:2','2:3']


def test_all_24_drafts_prepare_prompts_and_manual_receipts_without_adoption(image_workspace):
    project, service, _ = image_workspace
    from image_ai_fixtures import references_ready
    references_ready(image_workspace)
    clear_directions(service)
    before = {p:p.read_bytes() for p in (service.artwork.story_store.path, service.store.root/'editorial.json', service.store.root/'character_ai.json')}
    from manga.automation import Automation
    from manga.manual_image_ui import receipt
    from manga.character_ai_samples import example_image
    from manga.image_ai_models import REVIEW_FIELDS
    from manga.production_dashboard import dashboard
    batch = prepare_drafts(service.artwork)
    keys = [r['key'] for r in batch['items'] if not r['issues']]
    assert len(keys) == 24
    assert len(save_drafts(service.artwork,batch,keys,confirmed=True)['saved']) == 24
    for row in batch['items']:
        plan = service.plan('panel',page=row['page'],panel=row['panel'])
        assert receipt(plan)['story_version_id'] == batch['story_version_id']
    plan = service.plan('panel',page=1,panel=1)
    adopted_before = copy.deepcopy(service.store.load()['adoptions'])
    raw = example_image()
    import hashlib
    candidate = Automation(service.directory,project.id).import_manual(plan,json.dumps(receipt(plan)).encode(),raw,'TEST_ONLY_phase8.png',hashlib.sha256(raw).hexdigest())
    assert service.store.load()['adoptions'] == adopted_before
    with pytest.raises(ValueError):
        service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],confirmed=True)
    service.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'目視操作の模擬。人物一致と公開品質は未検証。',expected_revision=service.store.load()['revision'])
    assert service.store.load()['adoptions'] == adopted_before
    service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],confirmed=True)
    result = dashboard(project,service.directory)
    assert sum(bool(r['selected']) for r in result['panels']) == 1
    assert result['test_images'] and result['rows'][-1]['state'] != '完了'
    assert all(p.read_bytes()==content for p,content in before.items())


def test_mock_or_stale_material_does_not_complete_manual_steps():
    row = dict(page=1,panel=1,selected=True,stale=False,prompt_ready=True,direction=True,direction_stale=False)
    data = dict(jobs=[dict(id='j',plan=dict(kind='panel',page=1,panel=1,material_token='now'))],
                candidates=[dict(id='c',job_id='j',provider='mock',mock=True,sha256='a')],reviews=[])
    steps = step_states(row,data,'now',finalized=True,prepared=True)
    assert steps[3]['state'] != '完了' and steps[5]['state'] != '完了' and steps[6]['state'] != '完了'
    row.update(stale=True,direction_stale=True)
    assert step_states(row,data,None,finalized=False,prepared=False)[1]['state'] == '要再確認'
