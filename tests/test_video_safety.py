import copy
import json
import os
import sys

import pytest

from manga.storage import StorageError
from manga.video_sources import file_bytes
from manga.video_process import run,VideoError,VideoCancelled
from video_fixtures import video_workspace,protected,create_job


@pytest.mark.parametrize('kind',['image_missing','image_modified','wav_missing','wav_modified','image_link'])
def test_missing_corrupt_or_link_material_is_not_a_completed_video(video_workspace,tmp_path,kind):
    service,timeline=video_workspace
    relative=timeline['assets'][0]['path'] if kind.startswith('wav') else timeline['scenes'][0]['image']['path']
    path=service.store.root/relative
    if kind.endswith('missing'):path.unlink()
    elif kind.endswith('link'):
        target=tmp_path/'outside.png';target.write_bytes(path.read_bytes());path.unlink();path.symlink_to(target)
    else:path.write_bytes(b'corrupt')
    with pytest.raises((ValueError,StorageError)):create_job(service,timeline)
    assert not service.store.load()['jobs'] and not service.store.load()['exports']


def test_stale_story_and_tampered_timeline_are_rejected(video_workspace):
    service,timeline=video_workspace
    story=copy.deepcopy(service.voice.source()[0]['story']);story['pages'][0]['summary']='変更'
    service.voice.stories.save(story,expected_version_id=timeline['story_version_id'])
    with pytest.raises(ValueError,match='古いタイムライン'):create_job(service,timeline)


@pytest.mark.parametrize('path',['../outside','/etc/passwd','video/../voice.json','video\\oops'])
def test_path_escape_and_symlinks_are_rejected(video_workspace,path):
    service,timeline=video_workspace
    with pytest.raises((ValueError,StorageError)):service.store.safe(path)
    with pytest.raises((ValueError,StorageError)):file_bytes(service.store.root,'../outside',100)


def test_missing_ffmpeg_only_disables_video(video_workspace,monkeypatch):
    import manga.video_prereqs as prereqs
    service,timeline=video_workspace
    monkeypatch.setattr(prereqs.shutil,'which',lambda name:None)
    with pytest.raises(ValueError,match='ffmpeg'):create_job(service,timeline)
    assert service.voice.plan()['rows'] and service.voice.store.load()['assets']


def test_same_project_concurrent_job_and_plan_edits_are_refused(video_workspace):
    service,timeline=video_workspace;job=create_job(service,timeline)
    with pytest.raises(StorageError,match='実行中'):create_job(service,timeline)
    with pytest.raises(StorageError,match='動画処理中'):service.save_plans(timeline['id'],service.plans(timeline),expected_revision=service.store.load()['revision'])
    with service.store.lock(generation=True):
        with pytest.raises(StorageError):service.recover(job['id'])


def test_low_disk_space_fails_before_start(video_workspace,monkeypatch):
    import shutil
    service,timeline=video_workspace
    monkeypatch.setattr(shutil,'disk_usage',lambda p:type('Disk',(),dict(free=0))())
    with pytest.raises(ValueError,match='空き容量'):create_job(service,timeline)
    assert not service.store.load()['jobs']


def test_interrupted_job_can_be_closed_without_deleting_history(video_workspace):
    service,timeline=video_workspace;job=create_job(service,timeline);before=protected(service)
    with pytest.raises(ValueError,match='中断状態'):service.recover(job['id'])
    assert service.store.load()['jobs'][0]['state']=='interrupted' and protected(service)==before
    assert create_job(service,timeline)['id']!=job['id']


def test_failed_ffmpeg_does_not_publish_and_retry_is_free(video_workspace,monkeypatch):
    import manga.video_renderer as renderer
    service,timeline=video_workspace;before=protected(service)
    monkeypatch.setattr(renderer,'run',lambda *a,**k:(_ for _ in ()).throw(VideoError('simulated failure')))
    job=create_job(service,timeline)
    with pytest.raises(ValueError):service.run_job(job['id'])
    assert service.store.load()['jobs'][0]['state']=='failed' and not service.store.load()['exports']
    assert protected(service)==before and create_job(service,timeline)['state']=='queued'


def test_timeout_and_cancel_terminate_real_child_process(tmp_path):
    with pytest.raises(VideoError,match='制限時間'):run([sys.executable,'-c','import time; time.sleep(10)'],cwd=tmp_path,timeout=.05)
    with pytest.raises(VideoCancelled):run([sys.executable,'-c','import time; time.sleep(10)'],cwd=tmp_path,cancel=lambda:True)


def test_zero_length_and_bad_json_are_not_overwritten(video_workspace):
    service,timeline=video_workspace
    path=service.store.root/timeline['assets'][0]['path'];path.write_bytes(b'')
    with pytest.raises(ValueError):create_job(service,timeline)
    service.store.path.write_text('{invalid')
    with pytest.raises(StorageError):service.store.load()
    assert service.store.path.read_text()=='{invalid'
