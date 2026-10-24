import copy
import json

import pytest

from manga.storage import StorageError
from story_ai_fixtures import ai_workspace,concepts,job


def finished(service):
    config=service.store.load()['settings'];config['max_cycles']=1
    service.configure(config,expected_revision=service.store.load()['revision']);concepts(service)
    service.run_job(job(service,'story')['id'])
    return service.store.load()['candidates'][-1]


def test_explicit_adoption_appends_compatible_story_and_maps_only_matching_evaluation(ai_workspace):
    service=ai_workspace;selected=finished(service);history=service.stories.load_history();characters=service.artwork.path.read_bytes()
    with pytest.raises(ValueError):service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=history[-1]['id'])
    result=service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=history[-1]['id'],confirmed=True)
    saved=service.stories.load_history();assert saved[:-1]==history and saved[-1]['id']==result['story_version_id'] and saved[-1]['story']==selected['story']
    evaluation=service.editorial.load()['evaluations'][-1]
    assert evaluation['payload']['story_version_id']==saved[-1]['id'] and evaluation['source_story']==selected['story']
    assert service.artwork.path.read_bytes()==characters
    for name in ('voice','video','images'):assert (service.store.root/name/'existing.bin').read_bytes()==b'preserve existing '+name.encode()
    repeat=service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=saved[-1]['id'],confirmed=True)
    assert result==repeat and len(service.stories.load_history())==2


@pytest.mark.parametrize('failure_path',['story_history.json','editorial.json','story_ai.json'])
def test_cross_file_adoption_failure_recovers_without_duplicate_version(ai_workspace,monkeypatch,failure_path):
    import manga.story_ai_adoption as adoption
    service=ai_workspace;selected=finished(service);history=service.stories.load_history();original=adoption.write_json_atomic
    def fail(path,data):
        if path.name==failure_path:raise OSError('simulated failure')
        return original(path,data)
    monkeypatch.setattr(adoption,'write_json_atomic',fail)
    with pytest.raises(OSError):service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=history[-1]['id'],confirmed=True)
    with pytest.raises(StorageError):service.configure(service.store.load()['settings'],expected_revision=service.store.load()['revision'])
    monkeypatch.setattr(adoption,'write_json_atomic',original)
    result=service.recover_adoption();assert len(service.stories.load_history())==2
    assert service.recover_adoption()==result and len(service.store.load()['adoptions'])==1
    service.configure(service.store.load()['settings'],expected_revision=service.store.load()['revision'])
    assert service.recover_adoption()==result


def test_conflicting_later_edit_stops_adoption_recovery_without_overwriting(ai_workspace,monkeypatch):
    import manga.story_ai_adoption as adoption
    service=ai_workspace;selected=finished(service);original=adoption.write_json_atomic
    def fail(path,data):
        if path.name=='editorial.json':raise OSError('interrupted')
        return original(path,data)
    monkeypatch.setattr(adoption,'write_json_atomic',fail)
    with pytest.raises(OSError):service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=service.stories.load_history()[-1]['id'],confirmed=True)
    monkeypatch.setattr(adoption,'write_json_atomic',original)
    latest=service.stories.load_history()[-1];edited=copy.deepcopy(latest['story']);edited['summary']='後から人間が編集した内容'
    service.stories.save(edited,expected_version_id=latest['id']);before=service.stories.path.read_bytes()
    with pytest.raises(StorageError,match='別の編集'):service.recover_adoption()
    assert service.stories.path.read_bytes()==before


def test_stale_ui_version_and_different_page_count_do_not_adopt(ai_workspace):
    service=ai_workspace;selected=finished(service);before=service.stories.path.read_bytes()
    with pytest.raises(StorageError):service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=None,confirmed=True)
    from manga.storage import ProjectStore
    project=ProjectStore(service.directory).load(service.project_id);project.page_count=3;ProjectStore(service.directory).save(project)
    with pytest.raises(ValueError,match='ページ数'):service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=service.stories.load_history()[-1]['id'],confirmed=True)
    assert service.stories.path.read_bytes()==before


def test_corrupt_resume_snapshot_cannot_drop_existing_story_history(ai_workspace,monkeypatch):
    import manga.story_ai_adoption as adoption
    service=ai_workspace;selected=finished(service);original=adoption.write_json_atomic;before=service.stories.path.read_bytes()
    def fail(path,data):
        if path.name=='story_history.json':raise OSError('stop before write')
        return original(path,data)
    monkeypatch.setattr(adoption,'write_json_atomic',fail)
    with pytest.raises(OSError):service.adopt(selected['id'],expected_revision=service.store.load()['revision'],expected_story_id=service.stories.load_history()[-1]['id'],confirmed=True)
    monkeypatch.setattr(adoption,'write_json_atomic',original)
    path=service.store.safe('story_ai/adoption_pending.json');journal=json.loads(path.read_text())
    target=next(t for t in journal['targets'] if t['path']=='story_history.json');target['after']['versions']=target['after']['versions'][-1:]
    path.write_text(json.dumps(journal))
    with pytest.raises(StorageError,match='追記条件'):service.recover_adoption()
    assert service.stories.path.read_bytes()==before
