import copy
import hashlib
import io
import wave
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from manga.storage import StorageError
from manga.voice_audio import MockVoice,inspect_wav,normalize_wav
from manga.voice_models import voice_settings
from manga.voice_service import VoiceService
from manga.voice_timeline import save_timeline,timeline_is_current
from manga.voicevox import Voicevox,SOFTWARE_TERMS
from voice_fixtures import voice_workspace,all_ids
from test_voicevox import EngineFake,settings


def rights(confirmed=True):
    return dict(source='検証トーン・実際の日本語品質は未検証',software_terms=SOFTWARE_TERMS,
                library_terms='TEST ONLY library terms',credit='VOICEVOX:検証話者（フェイク）',
                software_confirmed=confirmed,library_confirmed=confirmed)


def manual_setup(service):
    service.save_voices({k:voice_settings(dict(provider='manual-wav',engine='manual',voice_id='manual')) for k in service.speakers()},expected_revision=service.store.load()['revision'])


def source_wav(rate=24000,channels=2):
    data=io.BytesIO()
    with wave.open(data,'wb') as wav:
        wav.setnchannels(channels);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(b'\1\0'*rate*channels)
    return data.getvalue()


def imported(service,identifier=None,content=None,**kw):
    identifier=identifier or all_ids(service)[0]
    return service.import_wav(identifier,content or source_wav(),target=service.voice_target(identifier),rights=rights(),expected_revision=service.store.load()['revision'],**kw)


def adopt(service,asset):
    service.review_audio(asset['id'],expected_revision=service.store.load()['revision'])
    service.select_asset(asset['provenance']['target']['utterance_id'],asset['id'],expected_revision=service.store.load()['revision'])


def test_manual_conversion_provenance_separate_review_and_assignment(voice_workspace):
    service,*_=voice_workspace;manual_setup(service)
    original=source_wav();asset=imported(service,content=original)
    assert asset['provenance']['original_sha256']==hashlib.sha256(original).hexdigest()
    assert service.store.read_blob(asset['provenance']['original_path'])==original
    assert inspect_wav(service.audio_bytes(asset))['duration_ms']==1000
    assert asset['info']['sha256']!=asset['provenance']['original_sha256']
    assert not asset['mock'] and not service.store.load()['assignments']
    assert service.plan()['rows'][0]['asset'] is None
    with pytest.raises(ValueError,match='試聴'):service.select_asset(all_ids(service)[0],asset['id'],expected_revision=service.store.load()['revision'])
    service.review_audio(asset['id'],expected_revision=service.store.load()['revision'])
    assert service.plan()['rows'][0]['asset'] is None
    service.select_asset(all_ids(service)[0],asset['id'],expected_revision=service.store.load()['revision'])
    assert service.plan()['rows'][0]['asset']['id']==asset['id']
    assert not service.artwork.load()['panels']


@pytest.mark.parametrize('field',['project_id','story_version_id','speaker_id','utterance_id','spoken_text','cache_key'])
def test_wrong_receipt_rejected_without_write(voice_workspace,field):
    service,*_=voice_workspace;manual_setup(service)
    identifier=all_ids(service)[0];target=service.voice_target(identifier);target[field]=str(uuid4())
    before=service.store.path.read_bytes()
    with pytest.raises(ValueError,match='一致'):service.import_wav(identifier,source_wav(),target=target,rights=rights(),expected_revision=service.store.load()['revision'])
    assert service.store.path.read_bytes()==before and not list(service.store.root.glob('voice/original/*'))


def test_unreviewed_rights_stored_but_not_adopted(voice_workspace):
    service,*_=voice_workspace;manual_setup(service);identifier=all_ids(service)[0]
    asset=service.import_wav(identifier,source_wav(),target=service.voice_target(identifier),rights=rights(False),expected_revision=service.store.load()['revision'])
    with pytest.raises(ValueError,match='利用条件'):service.review_audio(asset['id'],expected_revision=service.store.load()['revision'])
    assert not service.store.load()['audio_reviews'] and not service.store.load()['assignments']


def test_restart_idempotency_conflict_and_existing_files_kept(voice_workspace):
    service,*_=voice_workspace;manual_setup(service);asset=imported(service)
    before=service.store.path.read_bytes();restarted=VoiceService(service.directory,service.project_id)
    assert imported(restarted)['id']==asset['id'] and before==service.store.path.read_bytes()
    with pytest.raises(StorageError):service.import_wav(all_ids(service)[0],source_wav(),target=service.voice_target(all_ids(service)[0]),rights=rights(),expected_revision=0)
    replacement=imported(service,content=source_wav(16000,1))
    assert replacement['id']!=asset['id'];service.audio_bytes(asset)
    assert len(service.store.load()['assets'])==2


def test_concurrent_import_is_serialized(voice_workspace):
    service,*_=voice_workspace;manual_setup(service);identifier=all_ids(service)[0]
    revision=service.store.load()['revision'];target=service.voice_target(identifier)
    def run(_):
        try:return service.import_wav(identifier,source_wav(),target=target,rights=rights(),expected_revision=revision)
        except StorageError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sum(r is not None for r in results)==1 and len(service.store.load()['assets'])==1


@pytest.mark.parametrize('stage',['conversion','staging_wav','publish','json'])
def test_save_failure_recovery_without_new_generation(voice_workspace,monkeypatch,stage):
    service,*_=voice_workspace;manual_setup(service)
    import manga.voice_local as module
    original_write=service.store.write_blob;original_save=service.store.save;original_convert=module.normalize_wav
    def fail_write(path,content):
        if (stage=='publish' and path.startswith('voice/audio/')) or (stage=='staging_wav' and path.startswith('voice/staging/') and path.endswith('.wav')):raise OSError('disk full')
        return original_write(path,content)
    def fail_save(data,kind,**kw):
        if stage=='json' and kind=='job_ready':raise OSError('json failure')
        return original_save(data,kind,**kw)
    monkeypatch.setattr(service.store,'write_blob',fail_write);monkeypatch.setattr(service.store,'save',fail_save)
    if stage=='conversion':monkeypatch.setattr(module,'normalize_wav',lambda _:(_ for _ in ()).throw(ValueError('conversion')))
    with pytest.raises(ValueError,match='保存'):imported(service)
    job=service.store.load()['jobs'][0];assert job['state']=='artifact_pending'
    with pytest.raises(ValueError,match='復旧待ち'):imported(service)
    monkeypatch.setattr(service.store,'write_blob',original_write);monkeypatch.setattr(service.store,'save',original_save);monkeypatch.setattr(module,'normalize_wav',original_convert)
    recovered=VoiceService(service.directory,service.project_id).recover(job['id'])
    assert service.audio_bytes(recovered) and len(service.store.load()['jobs'])==1
    assert service.recover(job['id'])['id']==recovered['id'] and not service.store.load()['assignments']


def test_fake_voicevox_stays_free_cached_and_not_auto_adopted(voice_workspace,monkeypatch):
    service,*_=voice_workspace;fake=EngineFake();service.voicevox=Voicevox(send=fake)
    service.save_voices({k:settings() for k in service.speakers()},expected_revision=service.store.load()['revision'])
    import manga.budget.config as budget
    monkeypatch.setattr(budget,'open_budget',lambda *a:pytest.fail('free audio must not open ledger'))
    identifier=all_ids(service)[0];target=service.voice_target(identifier)
    asset=service.preview_voicevox(identifier,target=target,rights=rights(),expected_revision=service.store.load()['revision'],consent=True)
    repeated=service.preview_voicevox(identifier,target=target,rights=rights(),expected_revision=service.store.load()['revision'],consent=True)
    assert asset['id']==repeated['id'] and len(fake.calls)==4
    assert asset['provenance']['engine']['engine_version']=='test-0.26' and not service.store.load()['assignments']
    with pytest.raises(ValueError,match='1発話'):service.generate([identifier])
    adopt(service,asset);assert service.plan()['rows'][0]['asset']


def test_failed_engine_has_durable_stop_and_no_auto_resend(voice_workspace):
    service,*_=voice_workspace
    service.save_voices({k:settings() for k in service.speakers()},expected_revision=service.store.load()['revision'])
    calls=[]
    def fail(*args):calls.append(1);raise TimeoutError('secret')
    service.voicevox=Voicevox(send=fail);identifier=all_ids(service)[0]
    def run():return service.preview_voicevox(identifier,target=service.voice_target(identifier),rights=rights(),expected_revision=service.store.load()['revision'],consent=True)
    with pytest.raises(ValueError):run()
    with pytest.raises(ValueError,match='復旧待ち'):run()
    job=service.store.load()['jobs'][0]
    with pytest.raises(ValueError,match='自動再送'):service.recover(job['id'])
    assert len(calls)==1 and 'secret' not in service.store.path.read_text()
    service.close_resolved_job(job['id'],confirmed=True)
    assert service.store.load()['jobs'][0]['state']=='closed'


def test_timeline_real_duration_stale_after_audio_or_script_change(voice_workspace):
    service,cast,script,version=voice_workspace;manual_setup(service)
    for identifier in all_ids(service):adopt(service,imported(service,identifier))
    timeline=save_timeline(service,expected_revision=service.store.load()['revision'])
    assert all(s['end_ms']-s['start_ms']==1000 for s in timeline['subtitles'])
    assert all(a['human_reviewed'] and not a['mock'] for a in timeline['assets'])
    record=service.store.load()['timelines'][-1];assert timeline_is_current(service,record)
    asset=imported(service,content=source_wav(16000,1));assert timeline_is_current(service,record)
    adopt(service,asset);assert not timeline_is_current(service,record)
    old_paths=[a['path'] for a in service.store.load()['assets']]
    changed=copy.deepcopy(script);changed['pages'][0]['panels'][0]['description']+='変更'
    service.stories.save(changed,expected_version_id=version['id'])
    assert all(r['asset'] is None for r in service.plan()['rows'])
    assert all(service.store.safe_path(p).is_file() for p in old_paths)
    with pytest.raises(ValueError):adopt(service,asset)


@pytest.mark.parametrize('part',['original','converted'])
def test_sha_tampering_blocks_reuse(voice_workspace,part):
    service,*_=voice_workspace;manual_setup(service);asset=imported(service)
    path=asset['path'] if part=='converted' else asset['provenance']['original_path']
    service.store.safe_path(path).write_bytes(source_wav(16000,1))
    with pytest.raises(ValueError):service.audio_bytes(asset)
    with pytest.raises(ValueError):adopt(service,asset)


@pytest.mark.parametrize('content',[b'',b'fake.wav',b'RIFF'+b'\0'*50,b'x'*(25*1024*1024+1)])
def test_invalid_wav(content):
    with pytest.raises(ValueError):normalize_wav(content)


def test_missing_ffmpeg_preserves_input_error(monkeypatch):
    import manga.voice_audio as module
    monkeypatch.setattr(module.shutil,'which',lambda _:None)
    with pytest.raises(ValueError,match='FFmpeg'):normalize_wav(source_wav())
