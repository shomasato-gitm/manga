import copy

import pytest

from manga.video_process import verify_mp4,VideoError
from manga.storage import StorageError
from video_fixtures import video_workspace,create_job


@pytest.mark.parametrize('duration',['NaN','Infinity','0','-1'])
def test_non_finite_mp4_metadata_fails_closed(monkeypatch,tmp_path,duration):
    import manga.video_process as process
    value=dict(streams=[dict(codec_type='video',codec_name='h264',width=1920,height=1080,pix_fmt='yuv420p',avg_frame_rate='24/1'),
        dict(codec_type='audio',codec_name='aac',duration=duration,sample_rate='48000',channels=2)],format=dict(duration=duration))
    monkeypatch.setattr(process,'probe',lambda path:value)
    with pytest.raises(VideoError):verify_mp4(tmp_path/'unused.mp4',(1920,1080),24,1000)


def test_symlinked_video_folder_is_not_followed(video_workspace,tmp_path):
    service,timeline=video_workspace
    outside=tmp_path/'outside';outside.mkdir()
    (service.store.root/'video').symlink_to(outside,target_is_directory=True)
    with pytest.raises(StorageError):service.store.immutable('video/exports/new.mp4',b'no')
    assert not list(outside.iterdir())


def test_silent_highlight_still_has_audio_and_video_streams(video_workspace):
    service,timeline=video_workspace;plans=service.plans(timeline)
    plan=plans['tiktok_vertical'];row=copy.deepcopy(plans['youtube_main']['rows'][-1]);row['gap_after_ms']=0
    plan['rows']=[row]
    service.save_plans(timeline['id'],plans,expected_revision=service.store.load()['revision'])
    job=create_job(service,timeline,['tiktok_vertical']);record=service.run_job(job['id'])[0]
    assert record['metadata']['audio_codec']=='aac' and record['metadata']['duration_ms']>=100
