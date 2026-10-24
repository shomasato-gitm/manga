import copy
import json

import pytest
from PIL import Image

from manga.storage import StorageError
from manga.video_models import compile_plan,validate_plan,default_plans
from manga.video_visuals import crop_box,canvas,subtitle_image,motion_filter
from manga.japanese_text import find_japanese_font,TextLayoutError
from video_fixtures import video_workspace,protected,create_job


def test_independent_plans_and_complete_utterance_selection(video_workspace):
    service,timeline=video_workspace;plans=service.plans(timeline)
    assert len(timeline['scenes'])==24 and plans['youtube_shorts']['rows'][0]['scene_id']!=plans['tiktok_vertical']['rows'][0]['scene_id']
    main=compile_plan(plans['youtube_main'],timeline)
    assert main['duration_ms']==timeline['duration_ms']
    assert main['subtitles']==timeline['subtitles']
    vertical=plans['tiktok_vertical'];scene=timeline['scenes'][0];speech=scene['utterances'][1]
    vertical['rows']=[dict(vertical['rows'][0],utterance_id=speech['id'],gap_after_ms=0)]
    validate_plan(vertical,timeline);compiled=compile_plan(vertical,timeline)
    assert compiled['duration_ms']==speech['end_ms']-speech['start_ms']
    assert compiled['subtitles'][0]['start_ms']==0


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(fps=0),lambda p:p.update(fps=True),lambda p:p.update(hook='x'*121),
    lambda p:p['rows'][0].update(focus=[0,0,float('nan'),1]),lambda p:p['rows'][0].update(focus=[.9,0,.2,1]),
    lambda p:p['rows'][0].update(motion="still;movie=/etc/passwd"),lambda p:p['rows'][0].update(utterance_id='bad'),
    lambda p:p['rows'].reverse(),lambda p:p['rows'][0].update(gap_after_ms=-1),lambda p:p.update(rows=[])])
def test_invalid_plan_rejected_without_writing(video_workspace,mutation):
    service,timeline=video_workspace;plan=service.plans(timeline)['youtube_main'];before=protected(service)
    mutation(plan)
    with pytest.raises(ValueError):validate_plan(plan,timeline)
    assert protected(service)==before


def test_history_restore_conflict_and_cancel_preserve_originals(video_workspace):
    service,timeline=video_workspace;before=protected(service);old=service.plans(timeline)
    changed=copy.deepcopy(old);changed['tiktok_vertical']['hook']='違うフック'
    service.save_plans(timeline['id'],changed,expected_revision=service.store.load()['revision'])
    with pytest.raises(StorageError):service.save_plans(timeline['id'],old,expected_revision=0)
    history=service.store.load()['plan_history'][0]
    service.restore_plans(history['id'],expected_revision=service.store.load()['revision'])
    assert service.plans(timeline)==old and len(service.store.load()['plan_history'])==3
    job=create_job(service,timeline);service.cancel(job['id'])
    with pytest.raises(ValueError):service.run_job(job['id'])
    assert service.store.load()['jobs'][-1]['state']=='cancelled' and not service.store.load()['exports']
    assert protected(service)==before


def test_focus_fallback_contains_all_edges_and_motion_is_allowlisted(video_workspace):
    service,timeline=video_workspace
    source=timeline['scenes'][0]['image'];content=(service.store.root/source['path']).read_bytes()
    edit=service.plans(timeline)['youtube_main']['rows'][0]
    edit.update(framing='focus',focus=[0,0,1,1])
    image,meta=canvas(content,(1080,1920),edit,'bottom')
    assert meta['focus_fallback'] and image.size==(1080,1920)
    assert crop_box((640,360),.5625,[.0,.0,1,1]) is None
    box=crop_box((640,360),.5625,[.82,.12,.14,.2]);assert box is not None and box[2]>=.96*640
    x,y,w,h=meta['foreground']
    # Entire foreground survives the maximum 4% zoom / pan crop.
    assert x>1080*.04 and x+w<1080*.96 and y>1920*.04 and y+h<1920*.96
    assert 'movie=' not in motion_filter('pan_right',48,(1080,1920),24)


def test_subtitle_injection_is_literal_and_overflow_glyphs_fail():
    font=find_japanese_font()
    image=subtitle_image("{\\an8}日本語 ' ; [x]",(1080,1920),font,'bottom')
    assert image.getbbox()[0]>0 and image.getbbox()[2]<1080
    with pytest.raises(TextLayoutError):subtitle_image('文'*3000,(1080,1920),font,'bottom')
    with pytest.raises(TextLayoutError):subtitle_image('\ue000',(1080,1920),font,'bottom')


def test_preview_does_not_modify_source_and_explicit_ack_required(video_workspace):
    service,timeline=video_workspace;before=protected(service)
    image,_=service.preview(timeline['id'],service.plans(timeline)['youtube_main'])
    assert image.startswith(b'\x89PNG')
    with pytest.raises(ValueError,match='同意'):service.create_job(timeline['id'],['youtube_main'],expected_revision=service.store.load()['revision'])
    assert protected(service)==before
