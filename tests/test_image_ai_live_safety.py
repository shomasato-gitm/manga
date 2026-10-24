import json
from datetime import timedelta

import pytest

from manga.budget.config import open_budget
from manga.image_ai_live_provider import GeminiImageAdapter,LiveError
from manga.storage import StorageError
from image_ai_fixtures import image_workspace,adopt,generate,references_ready
from test_image_ai_live import setup_live,create_live


def test_price_expiry_after_queue_stops_send_but_not_settled_recovery(image_workspace,monkeypatch):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    import datetime
    future=datetime.datetime.now(datetime.timezone.utc)+timedelta(days=31)
    with monkeypatch.context() as clock:
        clock.setattr('manga.budget.ledger._now_and_month',lambda now=None:(future.isoformat(),future.strftime('%Y-%m')))
        with pytest.raises(ValueError,match='期限'): service.run(job['id'],approved=True)
    assert not http.calls
    candidate=service.run(job['id'],approved=True)
    monkeypatch.setattr('manga.budget.ledger._now_and_month',lambda now=None:(future.isoformat(),future.strftime('%Y-%m')))
    assert service.recover(job['id'],confirmed=True)==candidate and len(http.calls)==1


@pytest.mark.parametrize('part',['reference','job_directory','lock','candidate'])
def test_symlinks_never_followed(image_workspace,tmp_path,part):
    values=setup_live(image_workspace); service,plan,_,_,http=values
    outside=tmp_path/'outside'; outside.write_bytes(b'UNTOUCHED')
    if part=='reference':
        path=service.store.root/plan['reference_images'][0]['path']; path.unlink(); path.symlink_to(outside)
        with pytest.raises((ValueError,StorageError)): create_live(values)
    else:
        job,_=create_live(values)
        if part=='job_directory':
            path=service.store.root/'image_ai'/'jobs'/job['id']; path.parent.mkdir(parents=True,exist_ok=True); path.symlink_to(tmp_path,target_is_directory=True)
        elif part=='lock': (service.store.root/'.image-ai-live-generation.lock').symlink_to(outside)
        else:
            path=service.store.root/'image_ai'/'candidates'/f"{job['candidate_id']}.png"; path.parent.mkdir(parents=True,exist_ok=True); path.symlink_to(outside)
        with pytest.raises((ValueError,StorageError)): service.run(job['id'],approved=True)
    assert outside.read_bytes()==b'UNTOUCHED'
    if part in ('reference','job_directory','lock'): assert not http.calls


def test_model_usage_exceeds_quote_records_actual_and_blocks_next_api(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    http.response['usageMetadata'].update(candidatesTokenCount=10000,totalTokenCount=10100)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    ledger=open_budget(service.directory); status=ledger.request_status(job['id'])
    assert status['state']=='settled' and status['actual_jpy']>status['quote_jpy']
    assert ledger.summary(service.project_id)['blocked']
    assert not service.store.load()['candidates'] and len(http.calls)==1


def test_unexpected_thinking_retains_precise_cost_and_requires_review(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    http.response['usageMetadata'].update(thoughtsTokenCount=1,totalTokenCount=1221)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert service.store.load()['jobs'][0]['error_code']=='usage_bounds_exceeded'
    with pytest.raises(ValueError): create_live(values,new_request=True)
    assert len(http.calls)==1


def test_unexpected_server_tool_or_cache_billing_stays_uncertain(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    http.response['usageMetadata']['toolUsePromptTokenCount']=1
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert open_budget(service.directory).request_status(job['id'])['state']=='uncertain'


def test_response_excludes_error_headers_diagnostics_and_never_logs_key(image_workspace,monkeypatch,capsys):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    secret='TEST_PRIVATE_VALUE_MUST_NEVER_PERSIST'
    monkeypatch.setenv('GEMINI_API_KEY',secret)
    http.response['headers']={'x-goog-api-key':secret}; http.response['error']={'message':secret}
    http.response['candidates'][0]['headers']={'key':secret}
    http.response['candidates'][0]['content']['parts'][0]['inlineData']['debug']=secret
    service.run(job['id'],approved=True)
    for path in service.store.root.rglob('*'):
        if path.is_file() and path.suffix in ('.json','.log'): assert secret.encode() not in path.read_bytes()
    captured=capsys.readouterr(); assert secret not in captured.out+captured.err


def test_bible_update_invalidates_frozen_request_before_charge(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    from manga.image_ai_samples import confirm_bible
    person=job['plan']['source']['characters'][0]['id']
    confirm_bible(image_workspace[2],person)
    with pytest.raises((ValueError,StorageError)): service.run(job['id'],approved=True)
    assert not http.calls and open_budget(service.directory).request_status(job['id']) is None


def test_already_reserved_request_cannot_be_sent_manually(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,quote=create_live(values)
    ledger=open_budget(service.directory)
    ledger.reserve(project_id=service.project_id,request_id=job['id'],provider='gemini-live',model=values[2]['model'],kind='image',max_jpy=quote['max_jpy'])
    with pytest.raises(ValueError,match='request_id'): service.run(job['id'],approved=True)
    with pytest.raises(ValueError): service.recover(job['id'],confirmed=True)
    assert ledger.request_status(job['id'])['state']=='reserved' and not http.calls


def test_estimate_then_competing_reservation_prevents_call(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    open_budget(service.directory).reserve(project_id=service.project_id,request_id='other-pipeline-race',provider='aws-polly',model='neural',kind='tts',max_jpy='490')
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert not http.calls and open_budget(service.directory).request_status(job['id']) is None


def test_fake_image_handoff_to_existing_voice_and_ffmpeg_input(image_workspace):
    references_ready(image_workspace)
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    live=service.run(job['id'],approved=True)
    workspace=(image_workspace[0],service,image_workspace[2])
    selected=adopt(workspace,live)
    for page in image_workspace[2].proposals.latest()['story']['pages']:
        for panel in page['panels']:
            if (page['page'],panel['panel'])!=(1,1): adopt(workspace,generate(workspace,page=page['page'],panel=panel['panel']))
    from manga.voice_service import VoiceService
    from manga.voice_models import narrator_id,voice_settings
    from manga.voice_timeline import save_timeline
    from manga.video_sources import snapshot
    voice=VoiceService(service.directory,service.project_id)
    voices={p['id']:voice_settings(dict(voice_id='mock-'+str(i+1))) for i,p in enumerate(service.artwork.load()['characters'])}
    voices[narrator_id(service.project_id)]=voice_settings(dict(voice_id='mock-4'))
    voice.save_voices(voices,expected_revision=voice.store.load()['revision'])
    voice.generate([r['id'] for r in voice.plan()['rows']])
    timeline=save_timeline(voice,expected_revision=voice.store.load()['revision'])
    checked=snapshot(voice,timeline['id'])
    assert len(checked['images'])==24 and checked['images']['p1-c1']['sha256']==selected['registered_sha256']
    assert len(http.calls)==1 and checked['timeline']['schema_version']==1


def test_multiple_reference_consent_order_is_stable(image_workspace):
    references_ready(image_workspace)
    values=setup_live(image_workspace,page=1,panel=2)
    service,plan,card,consents,http=values
    assert len(consents)==2
    first=service.estimate_live(plan,card,consents)
    second=service.estimate_live(plan,card,list(reversed(consents)))
    assert first['approval_token']==second['approval_token'] and first['input_sha256']==second['input_sha256']
    job,_=create_live(values); service.run(job['id'],approved=True)
    parts=http.calls[0]['body']['contents'][0]['parts']
    assert len(parts)==3 and len(http.calls)==1


def test_card_changed_after_approval_requires_new_estimate(image_workspace):
    values=setup_live(image_workspace); service,plan,card,consents,http=values
    quoted=service.estimate_live(plan,card,consents)
    card['fx_jpy_per_usd']='170'
    with pytest.raises(ValueError,match='再見積'): service.create_live_job(plan,card,consents,approval_token=quoted['approval_token'],confirmed=True,expected_revision=0)
    assert not http.calls and not service.store.load()['jobs']


def test_huge_response_and_fake_switch_cannot_send(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    service._live_adapter=GeminiImageAdapter()
    with pytest.raises(ValueError): service.run(job['id'],approved=True)
    assert not http.calls
    service._live_adapter=GeminiImageAdapter(fake_transport=http)
    http.response['candidates'][0]['content']['parts'][0]['inlineData']['data']='A'*(31*1024*1024)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert open_budget(service.directory).request_status(job['id'])['state']=='uncertain'


def test_recovery_rejects_swapped_request_response(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    service.run(job['id'],approved=True)
    path=service.store.safe(f"image_ai/jobs/{job['id']}/live_response.json")
    original=json.loads(path.read_bytes()); original['request_id']='different-request'
    path.write_text(json.dumps(original))
    with pytest.raises(StorageError): service.recover(job['id'],confirmed=True)
    assert len(http.calls)==1


def test_new_candidate_requires_fresh_job_without_deleting_prior_history(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; first,_=create_live(values)
    service.run(first['id'],approved=True)
    second,_=create_live(values,new_request=True)
    assert second['id']!=first['id'] and second['candidate_id']!=first['candidate_id']
    service.run(second['id'],approved=True)
    assert len(http.calls)==2 and len(service.store.load()['candidates'])==2 and len(service.store.load()['jobs'])==2


def test_provider_cannot_bypass_budgeted_executor(image_workspace):
    values=setup_live(image_workspace); service,_,card,_,http=values
    from manga.budget.guard import current_execution
    with pytest.raises(LiveError,match='BudgetedExecutor'): service.live_adapter.send(card,b'{}')
    assert not http.calls and current_execution() is None
    job,_=create_live(values); service.run(job['id'],approved=True)
    assert len(http.calls)==1 and current_execution() is None


def test_authentication_is_checked_before_quote_or_reservation(image_workspace,monkeypatch):
    # 将来no-goが解除されても、キーなしで送信ボタンや予算予約を有効にしない。
    # 能力チェックだけをfake化し、通信処理には到達しない。
    monkeypatch.setattr('manga.image_ai_live_provider.network_allowed',lambda:None)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    with pytest.raises(LiveError,match='GEMINI_API_KEY'): GeminiImageAdapter().check()
    assert open_budget(image_workspace[1].directory).summary(image_workspace[1].project_id)['project_used_jpy']==0
