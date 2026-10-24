import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from manga.storage import StorageError
from manga.voice_audio import inspect_wav
from manga.voice_models import narrator_id,voice_settings
from manga.voice_timeline import save_timeline,srt_text,timeline_is_current
from voice_fixtures import voice_workspace,all_ids


def test_extract_three_characters_and_narrator_in_reading_order(voice_workspace):
    service,cast,script,version=voice_workspace
    plan=service.plan();rows=plan['rows']
    assert len(rows)==5 and rows[0]['kind']=='narration'
    assert {r['speaker_id'] for r in rows}=={*(c['id'] for c in cast),narrator_id(service.project_id)}
    assert all(not r['needs_review'] for r in rows)
    assert '悠真：' not in rows[1]['spoken_text']
    assert len({r['settings']['voice_id'] for r in rows})==4
    assert plan['confirmed']


def test_generate_cache_and_changed_line_only(voice_workspace):
    service,cast,script,version=voice_workspace
    first=service.generate(all_ids(service))
    assert len(first)==5 and all(not a['cache_hit'] for a in first)
    for result in first:
        info=inspect_wav(service.audio_bytes(result['asset']))
        assert info['frames']>0 and info['sample_rate']==16000
    repeated=service.generate(all_ids(service))
    assert all(a['cache_hit'] for a in repeated) and len(service.store.load()['jobs'])==5
    changed=copy.deepcopy(script)
    changed['pages'][0]['panels'][2]['dialogue']='悠真：「本当のことを、話すよ。」'
    service.stories.save(changed,expected_version_id=version['id'])
    again=service.generate(all_ids(service))
    assert sum(a['cache_hit'] for a in again)==4
    assert len(service.store.load()['jobs'])==6
    assert service.stories.load_history()[0]['story']==script


def test_unknown_speaker_requires_manual_confirmation(voice_workspace):
    service,cast,script,version=voice_workspace
    changed=copy.deepcopy(script);changed['pages'][0]['panels'][1]['dialogue']='不明：「誰なの？」'
    service.stories.save(changed,expected_version_id=version['id'])
    plan=service.plan();row=next(r for r in plan['rows'] if r['speaker_name']=='不明')
    assert row['speaker_id'] is None and row['needs_review']
    with pytest.raises(ValueError):service.generate([row['id']])
    assert service.store.load()['jobs']==[]
    service.save_edit(row['id'],cast[2]['id'],'誰なの？','sad',expected_revision=service.store.load()['revision'],source_token=plan['token'])
    fresh=next(r for r in service.plan()['rows'] if r['id']==row['id'])
    assert not fresh['needs_review'] and fresh['settings']['emotion']=='sad'


def test_reading_edit_preserves_story_and_subtitle_and_invalidates_only_its_audio(voice_workspace):
    service,cast,script,version=voice_workspace
    service.generate(all_ids(service))
    before=service.stories.path.read_bytes();plan=service.plan();row=plan['rows'][1]
    service.save_edit(row['id'],row['speaker_id'],'あのさ、みさき。','whisper',expected_revision=service.store.load()['revision'],source_token=plan['token'])
    results=service.generate(all_ids(service))
    assert sum(r['cache_hit'] for r in results)==4
    timeline=save_timeline(service,expected_revision=service.store.load()['revision'])
    utterance=next(u for s in timeline['scenes'] for u in s['utterances'] if u['id']==row['id'])
    assert utterance['text']==row['text'] and utterance['spoken_text']=='あのさ、みさき。'
    assert service.stories.path.read_bytes()==before


def test_old_reading_not_silently_reused_after_script_change(voice_workspace):
    service,cast,script,version=voice_workspace
    plan=service.plan();row=plan['rows'][1]
    service.save_edit(row['id'],row['speaker_id'],'みさき','normal',expected_revision=1,source_token=plan['token'])
    changed=copy.deepcopy(script);changed['pages'][0]['panels'][0]['dialogue']='悠真：「違うセリフ」\n美咲：「先に話してもいい？」'
    service.stories.save(changed,expected_version_id=version['id'])
    fresh=next(r for r in service.plan()['rows'] if r['id']==row['id'])
    assert fresh['needs_review'] and '以前の読み' in ''.join(fresh['warnings'])


def test_voice_save_conflict_and_duplicate_names(voice_workspace):
    service,cast,script,version=voice_workspace
    before=service.store.path.read_bytes()
    with pytest.raises(StorageError):service.save_voices({},expected_revision=0)
    assert service.store.path.read_bytes()==before
    service.artwork.save_character({'name':'悠真'},expected_revision=service.artwork.load()['revision'])
    assert any(r['needs_review'] for r in service.plan()['rows'] if r['speaker_name']=='悠真')


@pytest.mark.parametrize('bad',[{'speed':float('nan')},{'speed':3},{'emotion':'unknown'},{'provider':'unknown'}])
def test_voice_settings_validation(bad):
    with pytest.raises(ValueError):voice_settings(bad)


def test_wav_srt_timeline_and_stale_detection(voice_workspace):
    service,cast,script,version=voice_workspace
    service.generate(all_ids(service))
    timeline=save_timeline(service,expected_revision=service.store.load()['revision'],gap_ms=123)
    assert len(timeline['scenes'])==24 and len(timeline['subtitles'])==5
    assert timeline['format']=='manga-voice-timeline' and timeline['schema_version']==1
    assert timeline['needs_review'] and all(a['mock'] for a in timeline['assets'])
    assets={a['id']:a for a in timeline['assets']}
    for scene in timeline['scenes']:
        for utterance in scene['utterances']:
            assert utterance['end_ms']-utterance['start_ms']==assets[utterance['asset_id']]['info']['duration_ms']
    assert '悠真：' not in srt_text(timeline) and '-->' in srt_text(timeline)
    record=service.store.load()['timelines'][-1]
    assert timeline_is_current(service,record)
    service.review_audio(timeline['assets'][0]['id'],expected_revision=service.store.load()['revision'])
    assert not timeline_is_current(service,record)
    assert json.loads(service.store.read_blob(record['paths']['json']))['id']==timeline['id']


def test_regeneration_keeps_previous_audio_and_can_restore_choice(voice_workspace):
    service,*_=voice_workspace
    identifier=all_ids(service)[0]
    original=service.generate([identifier])[0]['asset']
    replacement=service.generate([identifier],regenerate=True)[0]['asset']
    assert original['id']!=replacement['id'] and len(service.store.load()['assets'])==2
    service.audio_bytes(original)
    service.select_asset(identifier,original['id'],expected_revision=service.store.load()['revision'])
    assert service.plan()['rows'][0]['asset']['id']==original['id']


def test_concurrent_calls_share_cache_without_overwriting(voice_workspace):
    service,*_=voice_workspace
    ids=all_ids(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:service.generate(ids),range(2)))
    assert len(service.store.load()['jobs'])==5
    assert sum(a['cache_hit'] for batch in results for a in batch)==5


def test_saved_audio_failure_recovers_without_synthesis(voice_workspace,monkeypatch):
    service,*_=voice_workspace
    identifier=all_ids(service)[0];original=service.store.write_blob
    def fail(path,content):
        if path.startswith('voice/audio/'):raise OSError('simulated full disk')
        return original(path,content)
    monkeypatch.setattr(service.store,'write_blob',fail)
    with pytest.raises(ValueError):service.generate([identifier])
    job=service.store.load()['jobs'][0]
    monkeypatch.setattr(service.store,'write_blob',original)
    asset=service.recover(job['id'])
    assert len(service.store.load()['jobs'])==1 and asset['job_id']==job['id']


def test_corrupt_cache_and_symlink_fail_closed(voice_workspace,tmp_path):
    service,*_=voice_workspace
    asset=service.generate([all_ids(service)[0]])[0]['asset']
    path=service.store.safe_path(asset['path']);path.write_bytes(b'broken audio')
    with pytest.raises(ValueError):service.generate([all_ids(service)[0]])
    assert len(service.store.load()['jobs'])==1
    path.unlink();path.symlink_to(tmp_path/'outside.wav')
    with pytest.raises(ValueError):service.audio_bytes(asset)
    with pytest.raises(ValueError):service.store.safe_path('../outside.wav')


def test_bad_voice_json_is_not_replaced(voice_workspace):
    service,*_=voice_workspace
    service.store.path.write_text('{bad JSON')
    with pytest.raises(StorageError):service.store.load()
    assert service.store.path.read_text()=='{bad JSON'


def test_long_invalid_speech_remains_editable_without_touching_story(voice_workspace):
    service,cast,script,version=voice_workspace
    changed=copy.deepcopy(script);changed['pages'][0]['panels'][2]['dialogue']='悠真：「'+'あ'*3100+'」'
    service.stories.save(changed,expected_version_id=version['id'])
    plan=service.plan();row=plan['rows'][-1]
    assert row['needs_review'] and row['cache_key'] is None
    service.save_edit(row['id'],cast[0]['id'],'短く読んで確認する。','normal',expected_revision=service.store.load()['revision'],source_token=plan['token'])
    assert not service.plan()['rows'][-1]['needs_review']


def test_nfc_nfd_names_and_spoken_text_share_identity_and_cache(voice_workspace):
    service,cast,script,version=voice_workspace
    service.artwork.save_character({'name':'かさ\u3099ね'},character_id=cast[2]['id'],expected_revision=3)
    changed=copy.deepcopy(script);changed['pages'][0]['panels'][1]['dialogue']='かざね：「まなざし」'
    current=service.stories.save(changed,expected_version_id=version['id'])
    row=service.plan()['rows'][3]
    assert row['speaker_id']==cast[2]['id'] and not row['needs_review']
    service.generate([row['id']])
    changed['pages'][0]['panels'][1]['dialogue']='かざね：「まなさ\u3099し」'
    service.stories.save(changed,expected_version_id=current['id'])
    assert service.generate([row['id']])[0]['cache_hit']


@pytest.mark.parametrize('mutation',['path','duration','subtitle','speaker','overlap'])
def test_timeline_contract_rejects_bad_targets_and_timing(voice_workspace,mutation):
    from manga.voice_timeline import build_timeline
    from manga.voice_contract import validate_timeline
    service,*_=voice_workspace;service.generate(all_ids(service))
    value=build_timeline(service)
    if mutation=='path':value['assets'][0]['path']='../../outside.wav'
    elif mutation=='duration':value['assets'][0]['info']['duration_ms']+=1
    elif mutation=='subtitle':value['subtitles'][0]['end_ms']+=1
    elif mutation=='speaker':value['scenes'][0]['utterances'][0]['speaker_id']=value['assets'][1]['speaker_id']
    else:value['scenes'][1]['start_ms']=0
    with pytest.raises(ValueError):validate_timeline(value)
