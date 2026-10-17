import copy
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest

from manga.github_sync import SyncService,SyncConflict
from manga.git_transport import GitRemote
from manga.sync_manifest import build_manifest,validate_bundle,json_bytes,decode_json,MAX_FILE_BYTES
from manga.sync_pdca import set_policy,record_iteration
from manga.sync_outputs import save_output,output_index,current_output_fingerprint
from manga.artwork_storage import ArtworkStore
from manga.story_storage import StoryStore
from manga.storage import StorageError
from review_fixtures import create_review_example,sample_image


@pytest.fixture
def seeded(tmp_path):
    import subprocess
    bare=tmp_path/'remote.git';subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
    remote=GitRemote.for_local_test(bare,tmp_path/'cache')
    project,latest,art,pages=create_review_example(tmp_path/'client',page_count=1)
    service=SyncService(tmp_path/'client',project.id,remote)
    commit=remote.publish('exchange',None,None,{f'works/{project.id}/story.json':json_bytes(latest['story'])},'fixture')
    candidate=service.fetch_legacy('exchange',f'works/{project.id}/story.json',expected_revision=0)
    return service,remote,project,latest,art,pages,candidate,commit


def test_no_implicit_main_write_and_no_other_work_access(seeded):
    s,r,p,*_=seeded
    with pytest.raises(ValueError,match='main'):s.prepare_export('main',expected_revision=1)
    with pytest.raises(ValueError,match='main'):r.publish('main',None,None,{'data/projects/secrets.json':b'{}'},'unsafe')
    for path in ['data/projects/secrets.json',f'works/{p.id}/../other/story.json','/etc/passwd']:
        with pytest.raises(ValueError):s.fetch_legacy('exchange',path,expected_revision=1)
    assert r.head('main') is None


def test_revision_conflict_and_malformed_state_are_not_overwritten(seeded):
    s,r,p,latest,art,pages,candidate,_=seeded
    before=s.store.path.read_bytes()
    with pytest.raises(SyncConflict):s.adopt(candidate['id'],['story'],expected_revision=0)
    assert s.store.path.read_bytes()==before
    s.store.path.write_text('{broken')
    with pytest.raises(StorageError):s.store.load()
    assert s.store.path.read_text()=='{broken'


def test_sync_keeps_images_ids_reference_pin_and_all_existing_history(seeded):
    s,r,p,latest,art,pages,candidate,_=seeded
    plan=s.prepare_export('exchange',expected_revision=1)
    s.push(plan['id'],expected_revision=2)
    received=s.fetch_bundle('exchange',expected_revision=3)
    protected={path:path.read_bytes() for path in art.root.rglob('*') if path.is_file() and not path.name.startswith('.') and 'github_sync' not in path.parts}
    original=art.load();s.adopt(received['id'],['story','settings','images','composition'],expected_revision=4)
    updated=art.load()
    assert [c['id'] for c in updated['characters']]==[c['id'] for c in original['characters']]
    assert updated['characters'][0]['primary_reference_id']==original['characters'][0]['primary_reference_id']
    assert updated['panels']['1:1']['selection_history']==original['panels']['1:1']['selection_history']
    for path,content in protected.items():
        if path.name in ('artwork.json','composition.json','github_sync.json'):continue
        assert path.read_bytes()==content
    assert len(StoryStore(s.directory,p.id).load_history())==1


def test_reference_hash_and_id_collision_stop_before_writes(seeded):
    from manga.sync_adoption import adoption_plan
    from manga.sync_local import collect_local
    s,r,p,latest,art,pages,candidate,_=seeded
    files=collect_local(s.directory,p.id);manifest=build_manifest(p.id,files,story_version_id=latest['id'],source_commit='a'*40)
    altered=dict(files);values=decode_json(files['artwork.json']);values['characters'][0]['primary_reference_id']=str(uuid4())
    altered['artwork.json']=json_bytes(values)
    with pytest.raises(SyncConflict,match='基準画像ID'):adoption_plan(s.directory,p.id,files,altered,manifest,['settings'])
    altered=dict(files);image=values['characters'][0]['references'][0];altered[image['path']]=sample_image(color='blue')
    with pytest.raises(SyncConflict,match='ハッシュ'):adoption_plan(s.directory,p.id,files,altered,manifest,['settings'])


def test_two_sided_story_conflict_shows_diff_and_keeps_local(seeded):
    s,r,p,latest,art,pages,candidate,commit=seeded
    s.adopt(candidate['id'],['story'],expected_revision=1)
    changed=copy.deepcopy(latest['story']);changed['summary']='ローカルで変更した概要'
    StoryStore(s.directory,p.id).save(changed,expected_version_id=StoryStore(s.directory,p.id).load_history()[-1]['id'])
    remote_story=copy.deepcopy(latest['story']);remote_story['summary']='GitHub側の異なる概要'
    # 上書き不可のtransportでは新規ファイルとして次の提案を保存する。
    r.publish('exchange',commit,commit,{f'works/{p.id}/next_story.json':json_bytes(remote_story)},'new draft')
    before=art.path.read_bytes()
    with pytest.raises(SyncConflict) as error:s.fetch_legacy('exchange',f'works/{p.id}/next_story.json',expected_revision=2)
    assert error.value.differences and any(x['state']=='競合' for x in error.value.differences)
    assert art.path.read_bytes()==before


def test_pending_transaction_resume_keeps_backup_and_rejects_new_edits(seeded):
    s,r,p,latest,art,pages,candidate,_=seeded
    before=art.path.read_bytes()
    from manga.sync_storage import write_json_atomic
    def fail(path,data):
        if Path(path)==s.store.path:raise StorageError('ディスク不足')
        return write_json_atomic(path,data)
    with patch('manga.sync_storage.write_json_atomic',side_effect=fail):
        with pytest.raises(StorageError):s.adopt(candidate['id'],['story'],expected_revision=1)
    assert s.store.pending()
    with pytest.raises(SyncConflict):s.prepare_export('exchange',expected_revision=1)
    s.store.resume()
    assert s.store.pending() is None and art.path.read_bytes()==before
    assert list((s.store.root/'github_sync'/'transactions').rglob('before/story_history.json'))


def test_pdca_unknown_score_human_unconfirmed_limit_and_stop_reason(seeded):
    s,r,p,latest,art,pages,candidate,_=seeded
    set_policy(s.store,2,80,expected_revision=1)
    one=record_iteration(s,candidate['id'],expected_revision=2,score=100,confirmed=True)
    assert not one['confirmed'] and one['stop_reason']=='未確認の項目あり'
    two=record_iteration(s,candidate['id'],expected_revision=3,score=None,stop_reason='人間が制作方針を検討するため停止')
    assert two['state']=='停止' and '制作方針' in two['stop_reason']
    with pytest.raises(ValueError,match='上限'):record_iteration(s,candidate['id'],expected_revision=4)
    assert len(s.store.load()['iterations'])==2


def test_png_output_save_size_staleness_and_binary_export(seeded):
    from manga.page_render import render_page
    from manga.japanese_text import find_japanese_font
    from manga.page_exports import export_book
    s,r,p,latest,art,pages,candidate,_=seeded
    fp=current_output_fingerprint(s.directory,p.id)
    png=render_page(latest['story']['pages'][0],latest['id'],art.load(),pages.load(),art,find_japanese_font()).png
    item=save_output(s.directory,p.id,'page_001.png',png,fp)
    assert (art.root/item['path']).read_bytes()==png
    with pytest.raises(ValueError,match='保存済み'):save_output(s.directory,p.id,'page_001.png',png,fp)
    with pytest.raises(ValueError,match='10MiB'):save_output(s.directory,p.id,'too_large.pdf',b'x'*(MAX_FILE_BYTES+1),fp)
    pdf,_=export_book(latest['story'],latest['id'],art.load(),pages.load(),art,find_japanese_font(),'pdf')
    plan=s.prepare_export('exchange',expected_revision=1,outputs={'page_001.png':png,'book.pdf':pdf})
    files=s._files(plan,'exports')
    assert files['exports/page_001.png']==png and files['exports/book.pdf']==pdf
    assert not any('data/projects' in e['path'] for e in plan['manifest']['files'])
    art.save_style('後から作風を変更',expected_revision=art.load()['revision'])
    with pytest.raises(ValueError,match='変わりました'):save_output(s.directory,p.id,'old.png',png,fp)


def test_remote_success_local_log_failure_is_reconciled_without_second_push(seeded):
    s,r,p,*_=seeded
    plan=s.prepare_export('exchange',expected_revision=1)
    with patch.object(s.store,'save',side_effect=StorageError('記録だけ失敗')):
        with pytest.raises(StorageError):s.push(plan['id'],expected_revision=2)
    published=r.head('exchange')
    with patch.object(r,'publish',side_effect=AssertionError('二重push')):
        recovered=s.push(plan['id'],expected_revision=2)
    assert recovered['recovered'] and recovered['commit']==published
    assert s.store.load()['exports'][-1]['status']=='保存済み'


def test_manifest_image_pin_tampering_and_binary_corruption_are_rejected(seeded):
    from manga.sync_local import collect_local
    s,r,p,latest,*_=seeded
    files=collect_local(s.directory,p.id)
    manifest=build_manifest(p.id,files,story_version_id=latest['id'],source_commit='a'*40)
    manifest['characters'][0]['primary_reference_id']=str(uuid4())
    with pytest.raises(ValueError,match='manifest'):validate_bundle(manifest,files,p.id)
    manifest=build_manifest(p.id,files,story_version_id=latest['id'],source_commit='a'*40)
    image=next(path for path in files if path.startswith('images/'))
    broken=dict(files);broken[image]=b'not a PNG'
    manifest=build_manifest(p.id,broken,story_version_id=latest['id'],source_commit='a'*40)
    with pytest.raises((ValueError,OSError)):validate_bundle(manifest,broken,p.id)


@pytest.mark.parametrize('path',['../token','/etc/passwd','a/../secret','a//b','a/./b','a\\b','x\nfile','.git/config'])
def test_path_boundary_rejects_escape(path):
    from manga.sync_manifest import safe_path
    with pytest.raises(ValueError):safe_path(path)


@pytest.mark.parametrize('fault',['missing','type','duplicate','oversize','invalid_id'])
def test_legacy_invalid_data_stops_without_writing_canonical_files(seeded,fault):
    from manga.sync_legacy import legacy_bundle
    s,r,p,latest,art,pages,*_=seeded
    before=pages.stories.path.read_bytes()
    if fault=='missing':
        with pytest.raises(ValueError):legacy_bundle(p.id,'a'*40,json_bytes(latest['story']),json_bytes({'schema_version':1,'project_id':p.id}))
    elif fault=='type':
        bad=copy.deepcopy(latest['story']);bad['schema_version']=True
        with pytest.raises(ValueError):legacy_bundle(p.id,'a'*40,json_bytes(bad))
    elif fault=='duplicate':
        with pytest.raises(ValueError):legacy_bundle(p.id,'a'*40,b'{"pages":[],"pages":[]}')
    elif fault=='oversize':
        from manga.sync_manifest import check_limits
        with pytest.raises(ValueError):check_limits({'too-large.png':b'x'*(MAX_FILE_BYTES+1)})
    else:
        with pytest.raises(ValueError):SyncService(s.directory,'../../secret',r)
    assert pages.stories.path.read_bytes()==before


def test_only_selected_project_is_collected_and_credentials_never_persist(seeded,monkeypatch,tmp_path):
    from manga.sync_local import collect_local
    s,r,p,*_=seeded
    unrelated=s.directory/'other-secret.json';unrelated.write_text('private material')
    (s.store.root/'.env').write_text('GITHUB_TOKEN=never-upload-this')
    files=collect_local(s.directory,p.id)
    assert not any(b'never-upload-this' in b or b'private material' in b for b in files.values())
    remote=GitRemote('shomasato-gitm/manga',tmp_path/'private-cache',token='private-token-for-test')
    with remote._environment() as env:
        assert env['MANGA_GIT_TOKEN']=='private-token-for-test'
        script=Path(env['GIT_ASKPASS']).read_text()
        assert 'private-token-for-test' not in script
    assert not any(b'private-token-for-test' in path.read_bytes() for path in s.store.root.rglob('*.json'))


def test_missing_transaction_payload_is_an_error_not_a_completed_save(seeded):
    s,r,p,*rest=seeded;candidate=rest[-2]
    from manga.sync_storage import write_json_atomic
    def fail(path,data):
        if Path(path)==s.store.path:raise StorageError('stop')
        return write_json_atomic(path,data)
    with patch('manga.sync_storage.write_json_atomic',side_effect=fail):
        with pytest.raises(StorageError):s.adopt(candidate['id'],['story'],expected_revision=1)
    pending=s.store.pending();target=s.store.transaction_file(pending['id'],pending['files'][0]['name'])
    target.rename(target.with_suffix('.backup'))
    with pytest.raises(StorageError):s.store.pending()


def test_parallel_branch_fetch_does_not_mix_fetch_head(seeded):
    from concurrent.futures import ThreadPoolExecutor
    s,r,p,latest,art,pages,candidate,head=seeded
    a=r.publish('branch-a',head,None,{f'works/{p.id}/a.json':b'{}'},'branch a')
    b=r.publish('branch-b',head,None,{f'works/{p.id}/b.json':b'{}'},'branch b')
    branches=['branch-a','branch-b']*5
    with ThreadPoolExecutor(max_workers=4) as executor:observed=list(executor.map(r.head,branches))
    assert observed==[a,b]*5


def test_completed_transaction_does_not_rehash_images_on_every_screen_open(seeded):
    s,r,p,latest,art,pages,candidate,head=seeded
    s.adopt(candidate['id'],['story'],expected_revision=1)
    with patch('pathlib.Path.read_bytes',side_effect=AssertionError('完了した採用の画像を再読み込み')):
        assert s.store.pending() is None


def test_repeat_legacy_fetch_and_adopt_keeps_story_version_and_same_image_ids(seeded):
    s,r,p,latest,art,pages,candidate,head=seeded
    # 元の標準作品から、日時メタデータを持たない旧作画提案を作る。
    from manga.artwork_models import character_fields,direction_fields
    raw=art.load()
    proposal=dict(schema_version=1,project_id=p.id,style=raw['style'],characters=[dict(character_fields(c),id=c['id']) for c in raw['characters']],
        panels={k:dict(page=e['page'],panel=e['panel'],fields=direction_fields(e['direction']),source_description=e['direction']['source_panel']['description']) for k,e in raw['panels'].items()})
    path=f'works/{p.id}/proposal.json';picture_path=f'works/{p.id}/image.png'
    r.publish('exchange',head,head,{path:json_bytes(proposal),picture_path:sample_image()},'legacy artwork')
    first=s.fetch_legacy('exchange',f'works/{p.id}/story.json',artwork_path=path,images=[dict(path=picture_path,page=1,panel=1)],expected_revision=1)
    s.adopt(first['id'],['story','settings','images'],expected_revision=2)
    one=art.load();history=pages.stories.load_history()
    second=s.fetch_legacy('exchange',f'works/{p.id}/story.json',artwork_path=path,images=[dict(path=picture_path,page=1,panel=1)],expected_revision=3)
    s.adopt(second['id'],['story','settings','images'],expected_revision=4)
    two=art.load()
    assert pages.stories.load_history()==history
    assert [i['id'] for i in one['panels']['1:1']['candidates']]==[i['id'] for i in two['panels']['1:1']['candidates']]
    assert one['panels']['1:1']['candidates']==two['panels']['1:1']['candidates']


def test_unrelated_github_commit_does_not_invent_new_story_version(seeded):
    s,r,p,latest,art,pages,candidate,head=seeded
    r.publish('exchange',head,head,{f'works/{p.id}/unrelated_note.json':b'{}'},'unrelated change')
    next_candidate=s.fetch_legacy('exchange',f'works/{p.id}/story.json',expected_revision=1)
    assert candidate['source_commit']!=next_candidate['source_commit']
    assert candidate['manifest']['story_version_id']==next_candidate['manifest']['story_version_id']


def test_image_save_interruption_leaves_no_partial_target_and_can_resume(seeded,tmp_path):
    s,r,p,latest,art,pages,candidate,head=seeded
    plan=s.prepare_export('exchange',expected_revision=1);s.push(plan['id'],expected_revision=2)
    receiver=SyncService(tmp_path/'image_receiver',p.id,r)
    incoming=receiver.fetch_bundle('exchange',expected_revision=0)
    with patch('manga.sync_storage.os.link',side_effect=OSError('disk full')):
        with pytest.raises(OSError):receiver.adopt(incoming['id'],['story','settings','images'],expected_revision=1)
    assert receiver.store.pending()
    assert not list((receiver.store.root/'images').rglob('*.png'))
    receiver.store.resume()
    assert receiver.store.pending() is None
    other=ArtworkStore(receiver.directory,p.id)
    image=other.load()['panels']['1:1']['candidates'][0]
    assert other.image_bytes(image)==art.image_bytes(image)
