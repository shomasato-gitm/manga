import copy
import io
import json

import pytest

from manga.video_models import FORMATS
from manga.video_process import verify_mp4,VideoError
from scripts.video_measurements import measure
from video_fixtures import video_workspace,protected,create_job


def test_real_three_formats_sync_reuse_and_recovery(video_workspace,monkeypatch):
    import manga.video_renderer as renderer
    service,timeline=video_workspace;before=protected(service)
    original_run=renderer.run
    job=create_job(service,timeline);exports=service.run_job(job['id'])
    assert {e['kind'] for e in exports}==set(FORMATS)
    for record in exports:
        plan=job['plans'][record['kind']];path=service.store.safe(record['path'])
        result=verify_mp4(path,FORMATS[record['kind']],12,record['metadata']['duration_ms'])
        assert result['video_codec']=='h264' and result['audio_codec']=='aac' and result['audio_sample_rate']==48000
        measured=measure(path,plan,timeline)
        assert measured['max_audio_error_ms']<=250 and measured['max_subtitle_error_ms']<=250
        assert service.export_bytes(record)[:4]!=b'' and record['publication_review_required']
    assert protected(service)==before
    def forbidden(*a,**kw):raise AssertionError('Cached rendering must not execute FFmpeg again')
    monkeypatch.setattr(renderer,'run',forbidden)
    reused=create_job(service,timeline)
    assert reused['state']=='ready' and reused['cache_reused']
    assert set(reused['export_ids'])=={e['id'] for e in exports}
    assert len(service.store.load()['exports'])==3
    # New export history reuses verified clips, then fails after immutable files
    # were linked but before video.json was committed. Recovery must be idempotent.
    monkeypatch.setattr(renderer,'run',original_run)
    new=create_job(service,timeline,force=True)
    original=service.store.save
    def fail(data,kind,**fields):
        if kind=='job_ready':raise OSError('simulated metadata failure')
        return original(data,kind,**fields)
    monkeypatch.setattr(service.store,'save',fail)
    with pytest.raises(ValueError):service.run_job(new['id'])
    assert len(service.store.load()['exports'])==3
    monkeypatch.setattr(service.store,'save',original)
    recovered=service.recover(new['id']);assert len(recovered)==3
    assert len(service.recover(new['id']))==3 and len(service.store.load()['exports'])==6
    assert all(r['cache_hits']==r['scenes'] for r in recovered)
    assert protected(service)==before


def test_bgm_sfx_rights_mixing_and_audio_only_validation(video_workspace):
    service,timeline=video_workspace;content=(service.store.root/timeline['assets'][0]['path']).read_bytes()
    with pytest.raises(ValueError):service.add_audio(content,'music.wav',rights_reviewed=False,expected_revision=service.store.load()['revision'])
    record=service.add_audio(content,'music.wav',rights_reviewed=True,expected_revision=service.store.load()['revision'])
    plans=service.plans(timeline);plan=plans['tiktok_vertical']
    plan['bgm']=dict(id=record['id'],volume=.1,start_ms=0,fade_ms=100)
    plan['sfx']=[dict(id=record['id'],volume=.1,start_ms=200,fade_ms=50)]
    service.save_plans(timeline['id'],plans,expected_revision=service.store.load()['revision'])
    job=create_job(service,timeline,['tiktok_vertical']);result=service.run_job(job['id'])[0]
    assert result['metadata']['audio_codec']=='aac'
    with pytest.raises((ValueError,VideoError)):service.add_audio(b'not audio','secret.mp3',rights_reviewed=True,expected_revision=service.store.load()['revision'])


def test_stale_changes_while_rendering_never_become_ready(video_workspace,monkeypatch):
    import manga.video_jobs as jobs
    service,timeline=video_workspace
    original=jobs.render_format
    def changed(*a,**kw):
        result=original(*a,**kw)
        service.voice.review_audio(timeline['assets'][0]['id'],expected_revision=service.voice.store.load()['revision'])
        return result
    monkeypatch.setattr(jobs,'render_format',changed)
    job=create_job(service,timeline,['tiktok_vertical'])
    with pytest.raises(ValueError):service.run_job(job['id'])
    assert not service.store.load()['exports'] and service.store.load()['jobs'][0]['state']=='failed'
