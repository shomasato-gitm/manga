"""PR #1の認証・並行保存・中断復旧を、実作品へ書き込まず検証する。"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import subprocess
from threading import Event
from unittest.mock import patch

import pytest

from manga.git_transport import GitRemote
from manga.storage import ProjectStore, StorageError
from manga.sync_manifest import SyncConflict, json_bytes, build_manifest
from manga.sync_outputs import current_output_fingerprint, output_index, read_output, save_output
from test_sync_safety import seeded
from review_fixtures import sample_image


def test_explicit_token_disables_credential_helpers_and_never_enters_arguments(tmp_path):
    token='test-token-not-a-real-credential'
    remote=GitRemote('owner/repository',tmp_path,token)
    with patch('manga.git_transport.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as run:
        remote._run('ls-remote','origin')
    args=run.call_args.args[0]
    assert 'credential.helper=' in args
    assert token not in repr(args)
    assert token==run.call_args.kwargs['env']['MANGA_GIT_TOKEN']
    assert not Path(run.call_args.kwargs['env']['GIT_ASKPASS']).exists()
    # トークン未指定時のOSキーチェーン等は引き続き利用可能。
    remote.token=None
    with patch('manga.git_transport.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as run:
        remote._run('ls-remote','origin')
    assert 'credential.helper=' not in run.call_args.args[0]


def test_authentication_error_does_not_expose_helper_output(tmp_path):
    secret='test-secret-in-helper-stderr'
    remote=GitRemote('owner/repository',tmp_path,secret)
    with patch('manga.git_transport.subprocess.run',return_value=subprocess.CompletedProcess([],1,b'',secret.encode())):
        with pytest.raises(StorageError) as error:remote._run('fetch','origin')
    assert secret not in str(error.value)


def test_project_save_waits_for_sync_production_lock(seeded):
    service,_,project,*_=seeded
    started=Event()
    def save():
        started.set()
        return ProjectStore(service.directory).save(project)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with service.store.locked(production=True):
            future=executor.submit(save)
            assert started.wait(2)
            with pytest.raises(TimeoutError):future.result(timeout=0.15)
        assert future.result(timeout=3).id==project.id


def test_output_fsync_failure_never_leaves_a_partial_final_image(seeded):
    service,_,project,*_=seeded
    fp=current_output_fingerprint(service.directory,project.id)
    with patch('manga.sync_storage.os.fsync',side_effect=OSError('disk full')):
        with pytest.raises((OSError,StorageError)):
            save_output(service.directory,project.id,'page.png',sample_image(),fp)
    assert not list((service.store.root/'sync_outputs').glob('*.png'))
    assert output_index(service.directory,project.id)['items']==[]


def test_output_metadata_failure_resumes_same_binary_and_preserves_previous_file(seeded):
    service,_,project,*_=seeded
    fp=current_output_fingerprint(service.directory,project.id)
    first=save_output(service.directory,project.id,'first.png',sample_image(),fp)
    from manga.sync_storage import write_json_atomic
    def fail(path,data):
        if Path(path).name=='sync_outputs.json':raise StorageError('metadata disk full')
        return write_json_atomic(path,data)
    second=sample_image(color='blue')
    with patch('manga.sync_storage.write_json_atomic',side_effect=fail):
        with pytest.raises(StorageError):save_output(service.directory,project.id,'second.png',second,fp)
    pending=service.store.pending()
    assert pending and len(output_index(service.directory,project.id)['items'])==1
    assert read_output(service.directory,project.id,first)==sample_image()
    with pytest.raises(SyncConflict):service.prepare_export('exchange',expected_revision=1)
    service.store.resume()
    records=output_index(service.directory,project.id)['items']
    assert len(records)==2 and records[0]==first
    assert read_output(service.directory,project.id,records[1])==second
    assert service.store.pending() is None
    assert (service.store.area('transactions',pending['id'])/'before'/'sync_outputs.json').exists()


def test_corrupted_output_size_is_rejected_before_loading_the_binary(seeded):
    service,_,project,*_=seeded
    item=save_output(service.directory,project.id,'page.png',sample_image(),current_output_fingerprint(service.directory,project.id))
    with (service.store.root/item['path']).open('ab') as file:file.write(b'changed')
    with patch('pathlib.Path.read_bytes',side_effect=AssertionError('must check size first')):
        with pytest.raises(StorageError):read_output(service.directory,project.id,item)


def test_resume_conflict_preflights_every_file_and_keeps_new_local_edit(seeded):
    service,_,_,_,art,_,candidate,_=seeded
    from manga.sync_storage import write_json_atomic
    def fail(path,data):
        if Path(path)==service.store.path:raise StorageError('interrupted')
        return write_json_atomic(path,data)
    with patch('manga.sync_storage.write_json_atomic',side_effect=fail):
        with pytest.raises(StorageError):service.adopt(candidate['id'],['story'],expected_revision=1)
    original=service.store.path.read_bytes()
    # 中断中に他の編集画面から同期対象JSONが変更された場合は復旧を止める。
    changed=service.store.target('story_history.json')
    changed.write_bytes(b'{"external_edit":true}\n')
    with pytest.raises(SyncConflict):service.store.resume()
    assert changed.read_bytes()==b'{"external_edit":true}\n'
    assert service.store.path.read_bytes()==original and service.store.pending()


def test_direct_git_read_rejects_symlink_blob(seeded,tmp_path):
    _,remote,*_=seeded
    env={'GIT_INDEX_FILE':str(tmp_path/'symlink-index')}
    remote._run('read-tree','--empty',extra_env=env)
    oid=remote._run('hash-object','-w','--stdin',content=b'{}').decode().strip()
    remote._run('update-index','--add','--cacheinfo','120000',oid,'link.json',extra_env=env)
    tree=remote._run('write-tree',extra_env=env).decode().strip()
    env.update(GIT_AUTHOR_NAME='Test',GIT_AUTHOR_EMAIL='test@example.invalid',GIT_COMMITTER_NAME='Test',GIT_COMMITTER_EMAIL='test@example.invalid')
    commit=remote._run('commit-tree',tree,content=b'symlink fixture\n',extra_env=env).decode().strip()
    with pytest.raises(ValueError,match='通常ファイル'):remote.read(commit,'link.json')


def test_remote_advance_during_push_never_overwrites_other_commit(seeded,tmp_path):
    service,remote,project,*_=seeded
    plan=service.prepare_export('exchange',expected_revision=1)
    other=GitRemote.for_local_test(Path(remote.url),tmp_path/'other-cache')
    original=remote._run
    concurrent=[]
    def run(*args,**kwargs):
        if args[0]=='push':
            concurrent.append(other.publish('exchange',plan['parent'],plan['expected_head'],
                {f'works/{project.id}/concurrent.json':json_bytes({'preserved':True})},'concurrent writer'))
        return original(*args,**kwargs)
    with patch.object(remote,'_run',side_effect=run):
        with pytest.raises(SyncConflict):service.push(plan['id'],expected_revision=2)
    assert remote.head('exchange')==concurrent[0]
    assert remote.read(concurrent[0],f'works/{project.id}/concurrent.json')==json_bytes({'preserved':True})
    assert service.store.load()['exports'][-1]['status']=='確認待ち'


def test_new_project_file_created_after_preflight_is_never_overwritten(seeded):
    service,_,_,*_=seeded
    name='new_initial.json'
    target=service.store.target(name)
    from manga.sync_storage import os
    link=os.link
    def concurrent(source,destination):
        if Path(destination)==target:target.write_bytes(b'{"preserve":"other writer"}\n')
        return link(source,destination)
    with service.store.locked(production=True),patch('manga.sync_storage.os.link',side_effect=concurrent):
        with pytest.raises(SyncConflict):service.store.commit({name:None},{name:json_bytes({'new':True})})
    assert target.read_bytes()==b'{"preserve":"other writer"}\n'
    with pytest.raises(SyncConflict):service.store.resume()


def test_manifest_mismatch_stops_before_fetching_remaining_payloads(seeded):
    service,remote,project,latest,*_=seeded
    files={'story.json':json_bytes(latest['story']),'attachments/a.json':b'{}'}
    manifest=build_manifest(project.id,files,story_version_id=latest['id'],source_commit='a'*40)
    path=service.prefix+f'sync/{manifest["snapshot_id"]}/manifest.json'
    pointer=json_bytes({'schema_version':1,'project_id':project.id,'manifest_path':path})
    def read(commit,name):
        if name.endswith('/latest.json'):return pointer
        if name==path:return json_bytes(manifest)
        if name.endswith('/attachments/a.json'):return b'x'*1000
        raise AssertionError('must stop before the next payload')
    with patch.object(remote,'read',side_effect=read):
        with pytest.raises(ValueError,match='容量・ハッシュ'):service.fetch_bundle('exchange',expected_revision=1)
    assert len(service.store.load()['candidates'])==1


def test_legacy_file_count_limit_stops_before_network_fetch(seeded):
    service,remote,project,*_=seeded
    with patch.object(remote,'head',side_effect=AssertionError('too many files must not be fetched')):
        with pytest.raises(ValueError,match='300'):
            service.fetch_legacy('exchange',f'works/{project.id}/story.json',images=[{}]*300,expected_revision=1)


def test_image_symlink_cannot_write_into_a_different_local_project(seeded):
    service,_,_,*_=seeded
    other=service.directory/'another-work';other.mkdir()
    (service.store.root/'images'/'linked').symlink_to(other,target_is_directory=True)
    with service.store.locked(production=True):
        with pytest.raises(StorageError,match='別の作品'):
            service.store.commit({'images/linked/new.png':None},{'images/linked/new.png':sample_image()})
    assert list(other.iterdir())==[] and service.store.pending() is None


def test_export_plan_rechecks_output_source_after_network_io(seeded):
    service,remote,project,_,art,*_=seeded
    fp=current_output_fingerprint(service.directory,project.id)
    head=remote.head
    def change(branch):
        art.save_style('通信中に変更された作風',expected_revision=art.load()['revision'])
        return head(branch)
    with patch.object(remote,'head',side_effect=change):
        with pytest.raises(SyncConflict,match='生成元'):
            service.prepare_export('exchange',expected_revision=1,outputs={'page.png':sample_image()},output_fingerprint=fp)
    assert service.store.load()['exports']==[]
