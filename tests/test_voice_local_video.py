"""日本語の発話・対象対応をフェイクWAVで検証。音質/人物一致の実機証明ではない。"""
import pytest

from manga.voice_timeline import save_timeline
from manga.video_models import FORMATS
from manga.video_process import verify_mp4
from manga.voicevox import Voicevox
from video_fixtures import video_workspace,create_job
from test_voice_local import manual_setup,imported,adopt,rights
from test_voicevox import EngineFake,settings
from test_automation import prepared


@pytest.mark.parametrize('provider',['manual-wav','voicevox'])
def test_new_voice_sources_feed_three_real_mp4_formats(video_workspace,provider):
    service,old_timeline=video_workspace;voice=service.voice
    original={p:p.read_bytes() for p in (voice.stories.path,voice.artwork.path)}
    if provider=='manual-wav':manual_setup(voice)
    else:
        voice.save_voices({k:settings() for k in voice.speakers()},expected_revision=voice.store.load()['revision'])
        voice.voicevox=Voicevox(send=EngineFake())
    for row in voice.plan()['rows']:
        asset=imported(voice,row['id']) if provider=='manual-wav' else voice.preview_voicevox(row['id'],target=voice.voice_target(row['id']),rights=rights(),expected_revision=voice.store.load()['revision'],consent=True)
        adopt(voice,asset)
    timeline=save_timeline(voice,expected_revision=voice.store.load()['revision'],gap_ms=0,silent_panel_ms=100)
    assert len(timeline['scenes'])==24 and all(a['human_reviewed'] for a in timeline['assets'])
    plans=service.plans(timeline)
    for kind,plan in plans.items():
        plan.update(fps=12,quality='fast')
        if kind!='youtube_main':plan['rows']=plan['rows'][:3]
    service.save_plans(timeline['id'],plans,expected_revision=service.store.load()['revision'])
    job=create_job(service,timeline);exports=service.run_job(job['id'])
    assert {e['kind'] for e in exports}==set(FORMATS)
    for record in exports:
        info=verify_mp4(service.store.safe(record['path']),FORMATS[record['kind']],12,record['metadata']['duration_ms'])
        assert info['audio_codec']=='aac' and info['video_codec']=='h264'
        assert record['publication_review_required']
    assert all(p.read_bytes()==b for p,b in original.items())
    with pytest.raises(ValueError,match='古いタイムライン'):service.inspect(old_timeline['id'],service.plans(old_timeline))


def test_automation_stops_for_local_confirmation_then_uses_assigned_audio(prepared):
    p,s,t,e=prepared;manual_setup(e.voice)
    plan=e.plan();assert not any(a['kind'] in ('voice','timeline','video') for a in plan['actions'])
    assert any('1発話ずつ' in b for b in plan['blocked'])
    for row in e.voice.plan()['rows']:adopt(e.voice,imported(e.voice,row['id']))
    assert not any(a['kind']=='voice' for a in e.plan()['actions'])
    assert any(a['kind']=='timeline' for a in e.plan()['actions'])
