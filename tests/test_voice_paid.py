"""Paid execution paths with an in-process fake transport; no external APIs."""
import copy
from dataclasses import replace
from datetime import datetime,timedelta,timezone

import pytest

from manga.budget.config import open_budget
from manga.storage import StorageError
from manga.voice_models import voice_settings
from manga.voice_polly import PollyVoice,VoiceProviderError,polly_ssml,signed_request
from manga.voice_pricing import pricing_template
from voice_fixtures import voice_workspace,all_ids,paid_setup,fake_card


def approve(service,ids,regenerate=False):
    return service.estimate(ids,regenerate=regenerate)['approval_key']


def test_paid_requires_feature_flag_and_specific_consent(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    monkeypatch.delenv('MANGA_ENABLE_PAID_TTS')
    with pytest.raises(ValueError,match='無効'):service.generate(ids,approval=approve(service,ids))
    monkeypatch.setenv('MANGA_ENABLE_PAID_TTS','1')
    with pytest.raises(ValueError,match='明示承認'):service.generate(ids)
    assert not calls and not service.store.load()['jobs']


def test_fake_api_is_guarded_and_cached_without_more_ledger_rows(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)
    result=[service.generate([i],approval=approve(service,[i]))[0] for i in ids]
    assert len(calls)==5 and len(result)==5
    ledger=open_budget(service.directory)
    summary=ledger.summary(service.project_id)
    assert summary['requests_by_status']=={'settled':5} and not summary['blocked']
    for job in service.store.load()['jobs']:
        status=ledger.request_status(job['request_id'])
        assert status['model']=='neural' and status['actual_jpy']>0
    monkeypatch.delenv('MANGA_ENABLE_PAID_TTS')
    repeated=service.generate(ids)
    assert all(a['cache_hit'] for a in repeated) and len(calls)==5
    assert ledger.summary(service.project_id)==summary


def test_timeout_retains_quote_and_never_retries(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch,fail=True);ids=all_ids(service)[:1]
    with pytest.raises(ValueError) as error:service.generate(ids,approval=approve(service,ids))
    assert 'dummy-sensitive' not in str(error.value)
    ledger=open_budget(service.directory);summary=ledger.summary(service.project_id)
    assert summary['uncertain_count']==1 and summary['project_used_jpy']>0
    with pytest.raises(ValueError):service.generate(ids,approval=approve(service,ids))
    assert len(calls)==1 and len(service.store.load()['jobs'])==1


@pytest.mark.parametrize('usage',[None,'0','-1','NaN','1.5'])
def test_missing_usage_keeps_audio_for_manual_recovery(voice_workspace,monkeypatch,usage):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch,usage=usage);ids=all_ids(service)[:1]
    with pytest.raises(ValueError):service.generate(ids,approval=approve(service,ids))
    job=service.store.load()['jobs'][0]
    assert service.store.job_receipt(job)[0]['billed_characters'] in (None,0)
    assert not service.store.load()['assets']
    with pytest.raises(ValueError,match='未確定'):service.recover(job['id'])
    ledger=open_budget(service.directory)
    ledger.settle(job['request_id'],actual_jpy='0.01',confirmed_charge=True)
    asset=service.recover(job['id'])
    assert asset['job_id']==job['id'] and len(calls)==1


@pytest.mark.parametrize('after_commit',[False,True])
def test_paid_metadata_failure_resumes_same_artifact_without_resending(voice_workspace,monkeypatch,after_commit):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    original=service.store.save
    def fail(data,kind,**kw):
        if kind=='job_ready':
            if after_commit:original(data,kind,**kw)
            raise StorageError('simulated interrupted metadata write')
        return original(data,kind,**kw)
    monkeypatch.setattr(service.store,'save',fail)
    with pytest.raises(ValueError):service.generate(ids,approval=approve(service,ids))
    job=service.store.load()['jobs'][0]
    assert open_budget(service.directory).request_status(job['id'])['state']=='settled'
    monkeypatch.setattr(service.store,'save',original)
    service.recover(job['id']);service.recover(job['id'])
    assert len(calls)==1 and len(service.store.load()['assets'])==1


def test_500_cap_stops_before_a_job_or_transport(voice_workspace,monkeypatch):
    from uuid import uuid4
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    ledger=open_budget(service.directory)
    ledger.reserve(project_id=service.project_id,request_id=str(uuid4()),provider='other',model='fake',kind='test',max_jpy='500')
    with pytest.raises(ValueError,match='予算'):service.generate(ids,approval=approve(service,ids))
    assert calls==[] and service.store.load()['jobs']==[]


def test_unconfirmed_current_story_cannot_use_paid_tts(voice_workspace,monkeypatch):
    service,cast,script,version=voice_workspace;calls=paid_setup(service,monkeypatch)
    service.stories.save(script,expected_version_id=version['id'])
    ids=all_ids(service)[:1]
    with pytest.raises(ValueError,match='確定'):service.generate(ids,approval=approve(service,ids))
    assert calls==[]


def test_stale_approval_after_reading_change(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    approval=approve(service,ids);plan=service.plan();row=plan['rows'][0]
    service.save_edit(row['id'],row['speaker_id'],row['spoken_text']+'。','sad',expected_revision=service.store.load()['revision'],source_token=plan['token'])
    with pytest.raises(ValueError,match='承認'):service.generate(ids,approval=approval)
    assert not calls


def test_identical_spoken_lines_are_quoted_and_sent_once(voice_workspace,monkeypatch):
    service,cast,script,version=voice_workspace
    changed=copy.deepcopy(script)
    changed['pages'][0]['panels'][2]['dialogue']='悠真：「あのさ、美咲。」'
    current=service.stories.save(changed,expected_version_id=version['id'])
    service.editorial.finalize(expected_version_id=current['id'],expected_revision=1)
    calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    ids=all_ids(service)
    assert service.estimate(ids)['paid_calls']==4
    result=[service.generate([i],approval=approve(service,[i]))[0] for i in ids]
    assert len(calls)==4 and sum(r['cache_hit'] for r in result)==1


def test_price_template_expired_and_missing_proofs_are_not_usable(voice_workspace):
    service,*_=voice_workspace
    for card in (pricing_template(),{},[],'bad'):
        before=service.store.path.read_bytes()
        with pytest.raises((ValueError,TypeError)):service.save_pricing(card,expected_revision=service.store.load()['revision'])
        assert service.store.path.read_bytes()==before
    card=fake_card();card['verified_on']=(datetime.now().date()-timedelta(days=40)).isoformat()
    with pytest.raises(ValueError):service.save_pricing(card,expected_revision=service.store.load()['revision'])


def test_ssml_presets_escape_text_and_do_not_claim_unsupported_neural_styles():
    normal=voice_settings(dict(provider='aws-polly',engine='neural',voice_id='Takumi'))
    text=polly_ssml('「A & B」<こんにちは>',dict(normal,emotion='whisper'))
    assert '&amp;' in text and '&lt;' in text and '-8dB' in text
    assert 'amazon:effect' not in text


def test_signing_is_deterministic_and_secrets_never_enter_url_or_body():
    keys=dict(AWS_ACCESS_KEY_ID='test-access',AWS_SECRET_ACCESS_KEY='dummy-secret-value',AWS_SESSION_TOKEN='dummy-session')
    now=datetime(2026,10,24,tzinfo=timezone.utc)
    request=signed_request(b'{}','ap-northeast-1',keys,now=now)
    same=signed_request(b'{}','ap-northeast-1',keys,now=now)
    changed=signed_request(b'{"x":1}','ap-northeast-1',keys,now=now)
    assert request.get_header('Authorization')==same.get_header('Authorization')
    assert request.get_header('Authorization')!=changed.get_header('Authorization')
    assert request.full_url=='https://polly.ap-northeast-1.amazonaws.com/v1/speech'
    assert b'dummy-secret' not in request.data and 'dummy-secret' not in str(request.headers)
    with pytest.raises(ValueError):signed_request(b'{}','evil.example/',keys)


def test_provider_error_is_sanitized(tmp_path):
    from text_guard_fakes import guarded
    def fail(request):raise RuntimeError('dummy-secret-value')
    adapter=PollyVoice(send=fail,keys=dict(AWS_ACCESS_KEY_ID='test',AWS_SECRET_ACCESS_KEY='dummy-secret-value'))
    with pytest.raises(VoiceProviderError) as error:guarded(tmp_path,lambda:adapter.synthesize('<speak>確認</speak>',dict(engine='neural',voice_id='Takumi')),provider='aws-polly')
    assert 'dummy-secret' not in str(error.value)


def test_confirmed_no_charge_allows_explicit_new_job_not_auto_retry(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch,fail=True);ids=all_ids(service)[:1]
    with pytest.raises(ValueError):service.generate(ids,approval=approve(service,ids))
    job=service.store.load()['jobs'][0]
    with pytest.raises(ValueError):service.close_resolved_job(job['id'],confirmed=True)
    ledger=open_budget(service.directory)
    ledger.reconcile_no_charge(job['id'],confirmed_no_charge=True)
    with pytest.raises(ValueError):service.close_resolved_job(job['id'])
    service.close_resolved_job(job['id'],confirmed=True)
    paid_setup(service,monkeypatch)
    service.generate(ids,approval=approve(service,ids))
    data=service.store.load()
    assert len(data['jobs'])==2 and data['jobs'][0]['state']=='closed'
    assert data['jobs'][0]['id']!=data['jobs'][1]['id'] and len(calls)==1


def test_concurrent_paid_jobs_use_one_cache_and_single_charges(voice_workspace,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    approval=approve(service,ids)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:service.generate(ids,approval=approval),range(2)))
    assert len(calls)==1 and len(service.store.load()['jobs'])==1
    assert sum(x['cache_hit'] for batch in results for x in batch)==1


def test_changed_ledger_location_cannot_reset_project_budget(voice_workspace,monkeypatch,tmp_path):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    service.generate(ids,approval=approve(service,ids))
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'different'/'budget.sqlite3'))
    with pytest.raises(ValueError,match='保存先'):service.generate(ids,regenerate=True,approval=approve(service,ids,True))
    assert len(calls)==1 and len(service.store.load()['jobs'])==1


def test_unsupported_storage_stops_before_paid_operation(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch);ids=all_ids(service)[:1]
    def fail():raise StorageError('no hardlinks in this fixture')
    monkeypatch.setattr(service.store,'check_storage',fail)
    with pytest.raises(StorageError):service.generate(ids,approval=approve(service,ids))
    assert calls==[] and service.store.load()['jobs']==[]


def test_standard_whisper_and_voice_engine_compatibility():
    settings=voice_settings(dict(provider='aws-polly',engine='standard',voice_id='Mizuki',emotion='whisper'))
    assert 'amazon:effect name="whispered"' in polly_ssml('こんにちは',settings)
    with pytest.raises(ValueError):voice_settings(dict(provider='aws-polly',engine='neural',voice_id='Mizuki'))


def test_usage_overrun_is_recorded_and_blocks_further_calls(voice_workspace,monkeypatch):
    service,*_=voice_workspace;calls=paid_setup(service,monkeypatch,usage='100000');ids=all_ids(service)[:1]
    service.generate(ids,approval=approve(service,ids))
    ledger=open_budget(service.directory)
    assert ledger.summary(service.project_id)['unreviewed_overrun_count']==1
    assert ledger.summary(service.project_id)['project_used_jpy']==176
    with pytest.raises(ValueError):service.generate(ids,regenerate=True,approval=approve(service,ids,True))
    assert len(calls)==1
