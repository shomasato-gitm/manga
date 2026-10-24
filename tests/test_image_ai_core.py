import copy
from uuid import uuid4

import pytest

from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_samples import confirm_bible
from manga.storage import StorageError
from image_ai_fixtures import image_workspace,generate,adopt,references_ready,hashes


def test_reference_mock_candidate_is_private_then_explicitly_adopted(image_workspace):
    _,service,characters=image_workspace
    person=service.artwork.load()['characters'][1]
    before=hashes(service.store.root)
    candidate=generate(image_workspace,'character',character_id=person['id'])
    assert candidate['mock'] and service.artwork.load()['characters'][1]['primary_reference_id'] is None
    assert all(hashes(service.store.root)[k]==v for k,v in before.items())
    with pytest.raises(ValueError,match='目視'): adopt(image_workspace,candidate,skip_review=True)
    selected=adopt(image_workspace,candidate)
    current=service.artwork.load()['characters'][1]
    assert current['id']==person['id'] and current['primary_reference_id']==selected['image_id']
    assert current['references'][0]['original_name'].endswith('TEST_ONLY.png')
    with pytest.raises(ValueError,match='バイブル'): service.plan('panel',page=1,panel=2)
    confirm_bible(characters,person['id'])


def test_two_character_panel_uses_bibles_refs_and_clothing_exception(image_workspace):
    _,service,_=image_workspace
    with pytest.raises(ValueError,match='基準画像'): service.plan('panel',page=1,panel=2)
    references_ready(image_workspace)
    plan=service.plan('panel',page=1,panel=2)
    assert len(plan['bible_ids'])==2 and len(plan['reference_images'])==2
    assert '体操服' in plan['prompt'] and '体育の回想' in plan['prompt'] and '文字' in plan['prompt']
    assert plan['references_sent_to_external'] is False and plan['external_api_calls']==0
    assert service.artwork.load()['characters'][0]['clothing']=='制服'


def test_candidates_comparison_regeneration_and_reselection_keep_ids(image_workspace):
    _,service,_=image_workspace
    old=copy.deepcopy(service.artwork.load()['panels']['1:1'])
    first=generate(image_workspace); second=generate(image_workspace,new=True)
    assert first['id']!=second['id'] and first['sha256']!=second['sha256']
    chosen1=adopt(image_workspace,first); chosen2=adopt(image_workspace,second)
    art=service.artwork.load(); entry=art['panels']['1:1']
    assert entry['selected_image_id']==chosen2['image_id'] and len(entry['candidates'])==len(old['candidates'])+2
    again=adopt(image_workspace,first)
    entry=service.artwork.load()['panels']['1:1']
    assert again['image_id']==chosen1['image_id'] and entry['selected_image_id']==chosen1['image_id']
    assert len(entry['candidates'])==len(old['candidates'])+2 and len(entry['selection_history'])==len(old['selection_history'])+3
    revision=service.artwork.load()['revision']; adopt(image_workspace,first)
    assert service.artwork.load()['revision']==revision
    assert all(i in entry['candidates'] for i in old['candidates'])


def test_same_request_reuses_job_and_never_generates_implicitly(image_workspace):
    _,service,_=image_workspace
    plan=service.plan('panel',page=1,panel=1)
    first=service.create_job(plan,expected_revision=0)
    second=service.create_job(plan,expected_revision=service.store.load()['revision'])
    assert first['id']==second['id'] and not service.store.load()['candidates']
    image=service.run(first['id']); repeat=service.run(first['id'])
    assert image==repeat and len(service.store.load()['candidates'])==1


def test_adopting_other_panel_does_not_stale_target_candidate(image_workspace):
    _,service,_=image_workspace
    references_ready(image_workspace)
    one=generate(image_workspace,page=1,panel=1); two=generate(image_workspace,page=2,panel=1)
    adopt(image_workspace,one)
    adopt(image_workspace,two)
    assert service.artwork.load()['panels']['2:1']['selected_image_id']


@pytest.mark.parametrize('source',['story','bible','direction','style','layout','project'])
def test_source_change_blocks_stale_adoption_without_overwrite(image_workspace,source):
    _,service,characters=image_workspace
    candidate=generate(image_workspace)
    if source=='story':
        old=characters.proposals.latest(); characters.stories.save(old['story'],expected_version_id=old['id'])
    elif source=='bible': confirm_bible(characters,service.artwork.load()['characters'][0]['id'])
    elif source=='direction':
        d=service.artwork.load()['panels']['1:1']['direction']; service.artwork.save_direction(1,1,dict(d,pose='立つ'),story_version_id=d['story_version_id'],expected_revision=service.artwork.load()['revision'])
    elif source=='style': service.artwork.save_style('フルカラー',expected_revision=service.artwork.load()['revision'])
    elif source=='layout':
        from manga.page_storage import PageStore
        pages=PageStore(service.directory,service.project_id); settings=pages.load()['settings']; settings['width']=1600
        pages.save_settings(settings,expected_revision=0)
    else:
        from manga.storage import ProjectStore
        project=ProjectStore(service.directory).load(service.project_id); project.genre='SF'
        ProjectStore(service.directory).save(project)
    before=service.artwork.path.read_bytes()
    with pytest.raises((ValueError,StorageError)): adopt(image_workspace,candidate)
    assert service.artwork.path.read_bytes()==before and len(service.store.load()['candidates'])==1


def test_incomplete_visual_review_never_adopts(image_workspace):
    _,service,_=image_workspace; candidate=generate(image_workspace)
    before=service.artwork.path.read_bytes()
    with pytest.raises(ValueError,match='人間'): service.review(candidate['id'],dict(identity=True),'未完了',expected_revision=service.store.load()['revision'])
    assert service.artwork.path.read_bytes()==before
