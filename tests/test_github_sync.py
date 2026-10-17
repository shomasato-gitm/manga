"""同期境界を先に定義する。ネットワークと実作品の上書きは行わない。"""
import copy
import json
from pathlib import Path
from uuid import uuid4
import pytest

from manga.github_sync import SyncService, SyncConflict
from manga.git_transport import GitRemote
from manga.sync_manifest import build_manifest, validate_bundle, compare_files, sha256
from review_fixtures import create_review_example, sample_image


def test_three_way_conflict_and_json_diff():
    before={'story.json':b'{"value":1}'}
    local={'story.json':b'{"value":2}'}
    remote={'story.json':b'{"value":3}'}
    result=compare_files(before,local,remote)
    assert result[0]['state']=='競合' and result[0]['changes'][0]['path']=='value'
    assert compare_files(before,local,before)[0]['state']=='ローカル変更'
    assert compare_files(before,before,remote)[0]['state']=='GitHub変更'


def test_bundle_hash_schema_path_and_duplicate_keys_are_validated(tmp_path):
    project,latest,art,pages=create_review_example(tmp_path,page_count=1)
    files={'story.json':json.dumps(latest['story'],ensure_ascii=False).encode()}
    manifest=build_manifest(project.id,files,story_version_id=latest['id'],source_commit='a'*40)
    validate_bundle(manifest,files,project.id)
    assert json.loads(files['story.json'])['schema_version']==1
    with pytest.raises(ValueError):validate_bundle(manifest,{'story.json':b'changed'},project.id)
    bad=copy.deepcopy(manifest);bad['files'][0]['path']='../secret'
    with pytest.raises(ValueError):validate_bundle(bad,files,project.id)
    badfiles={'story.json':b'{"schema_version":1,"schema_version":1}'}
    bad=build_manifest(project.id,badfiles,story_version_id=latest['id'],source_commit='a'*40)
    with pytest.raises(ValueError):validate_bundle(bad,badfiles,project.id)


@pytest.fixture
def local_remote(tmp_path):
    import subprocess
    bare=tmp_path/'remote.git'
    subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
    remote=GitRemote.for_local_test(bare,tmp_path/'cache')
    return remote


def test_legacy_fetch_is_candidate_then_explicit_adoption_and_round_trip(tmp_path,local_remote):
    source=Path(__file__).resolve().parents[1]/'works'/'2d83c11f-04ee-4134-a66d-dace81c6f698'
    work_id=source.name
    original={p:p.read_bytes() for p in source.iterdir() if p.is_file()}
    raw={f'works/{work_id}/{p.name}':data for p,data in original.items()}
    commit=local_remote.publish('exchange',None,None,raw,'test fixture')
    service=SyncService(tmp_path/'client',work_id,local_remote)
    mapping=json.loads((source/'page_01_composition_manifest_2026-10-17.json').read_text())['sources']
    targets=[dict(path=f'works/{work_id}/{i["file"]}',page=1,panel=i['panel']) for i in mapping]
    candidate=service.fetch_legacy('exchange',f'works/{work_id}/story_test_revision_2026-10-17.json',
        artwork_path=f'works/{work_id}/artwork_directions_test_2026-10-17.json',images=targets,expected_revision=0)
    assert candidate['source_commit']==commit and candidate['status']=='確認待ち'
    assert not (tmp_path/'client'/work_id/'story_history.json').exists()
    service.adopt(candidate['id'],['story','settings','images'],expected_revision=1,adopt_selections=True)
    from manga.story_storage import StoryStore
    from manga.artwork_storage import ArtworkStore
    history=StoryStore(tmp_path/'client',work_id).load_history()
    artwork=ArtworkStore(tmp_path/'client',work_id).load()
    assert len(history[-1]['story']['pages'])==8 and len(artwork['panels'])==24
    assert sum(bool(e['selected_image_id']) for e in artwork['panels'].values())==3
    assert {c['id'] for c in artwork['characters']}=={'b8ed8ac7-60f1-4b8c-94d0-442ea3338740','33e85a7a-5ce1-4897-8dad-6240c491663d'}
    plan=service.prepare_export('exchange',expected_revision=2)
    pushed=service.push(plan['id'],expected_revision=3)
    assert pushed['commit']!=commit
    receiver=SyncService(tmp_path/'second',work_id,local_remote)
    received=receiver.fetch_bundle('exchange',expected_revision=0)
    receiver.adopt(received['id'],['story','settings','images'],expected_revision=1,adopt_selections=True)
    other=ArtworkStore(tmp_path/'second',work_id)
    assert {c['id'] for c in other.load()['characters']}=={c['id'] for c in artwork['characters']}
    for entry in artwork['panels'].values():
        for image in entry['candidates']:assert other.image_bytes(image)==ArtworkStore(tmp_path/'client',work_id).image_bytes(image)
    assert all(p.read_bytes()==data for p,data in original.items())


def test_local_edit_after_candidate_and_remote_edit_after_export_stop(tmp_path,local_remote):
    project,latest,art,pages=create_review_example(tmp_path/'client',page_count=1)
    service=SyncService(tmp_path/'client',project.id,local_remote)
    initial=local_remote.publish('exchange',None,None,{f'works/{project.id}/story.json':json.dumps(latest['story']).encode()},'seed')
    candidate=service.fetch_legacy('exchange',f'works/{project.id}/story.json',expected_revision=0)
    art.save_style('変更した作風',expected_revision=art.load()['revision'])
    before=art.path.read_bytes()
    with pytest.raises(SyncConflict):service.adopt(candidate['id'],['story'],expected_revision=1)
    assert art.path.read_bytes()==before
    plan=service.prepare_export('exchange',expected_revision=1)
    local_remote.publish('exchange',initial,initial,{f'works/{project.id}/new-note.txt':b'new'},'concurrent')
    with pytest.raises(SyncConflict):service.push(plan['id'],expected_revision=2)
