import base64
import copy
import hashlib
from datetime import date,timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from manga.budget.config import open_budget
from manga.image_ai_live_provider import GeminiImageAdapter,LiveError,_https
from manga.image_ai_live_samples import fake_card,FakeImageHTTP
from manga.image_ai_service import ImageAIService
from manga.storage import StorageError
from image_ai_fixtures import image_workspace,adopt,generate,hashes


def setup_live(workspace,kind='panel',**selection):
    _,original,_=workspace
    if kind=='panel' and not selection: selection=dict(page=1,panel=1)
    plan=original.plan(kind,**selection); http=FakeImageHTTP(plan)
    service=ImageAIService(original.directory,original.project_id,live_adapter=GeminiImageAdapter(fake_transport=http))
    card=fake_card(); consents=[r['id'] for r in plan['reference_images']]
    return service,plan,card,consents,http


def create_live(values,**kw):
    service,plan,card,consents,_=values
    quoted=service.estimate_live(plan,card,consents)
    job=service.create_live_job(plan,card,consents,approval_token=quoted['approval_token'],confirmed=True,expected_revision=service.store.load()['revision'],**kw)
    return job,quoted


def test_single_reference_request_settlement_candidate_and_explicit_adoption(image_workspace):
    values=setup_live(image_workspace); service,plan,card,consents,http=values
    art=service.artwork.path.read_bytes(); job,quote=create_live(values)
    assert not http.calls and quote['max_jpy']=='14.300000'
    assert open_budget(service.directory).summary(service.project_id)['project_used_jpy']==0
    with pytest.raises(ValueError): service.run(job['id'])
    candidate=service.run(job['id'],approved=True)
    assert len(http.calls)==1 and candidate['provider']=='gemini-live' and candidate['mock']
    assert service.artwork.path.read_bytes()==art
    request=http.calls[0]['body']; parts=request['contents'][0]['parts']
    assert len(parts)==len(consents)+1 and not request.get('tools')
    for part,ref in zip(parts[1:],plan['reference_images']):
        assert hashlib.sha256(base64.b64decode(part['inlineData']['data'])).hexdigest()==ref['sha256']
    status=open_budget(service.directory).request_status(job['id'])
    assert status['state']=='settled' and status['actual_jpy']==Decimal('5.9224')
    selected=adopt((image_workspace[0],service,image_workspace[2]),candidate)
    assert selected['image_id'] and len(service.store.load()['adoptions'])==1
    assert service.run(job['id'],approved=True)==candidate
    service.recover(job['id'],confirmed=True)
    assert len(http.calls)==1 and len(service.store.load()['candidates'])==1
    assert service.store.load()['jobs'][0]['state']=='ready'


def test_basis_without_references_and_mock_interoperability(image_workspace):
    person=next(c for c in image_workspace[1].artwork.load()['characters'] if not c['primary_reference_id'])
    values=setup_live(image_workspace,'character',character_id=person['id'])
    assert not values[3]
    job,_=create_live(values); candidate=values[0].run(job['id'],approved=True)
    assert len(values[4].calls[0]['body']['contents'][0]['parts'])==1
    offline=generate(image_workspace)
    assert offline['provider']=='mock' and len(values[0].store.load()['candidates'])==2


def test_production_always_closed_even_with_keys_flags_and_fake_card(image_workspace,monkeypatch):
    _,service,_=image_workspace
    monkeypatch.setenv('GEMINI_API_KEY','SENTINEL_PRIVATE_TEST_VALUE')
    monkeypatch.setenv('MANGA_ENABLE_IMAGE_API','1')
    plan=service.plan('panel',page=1,panel=1)
    before=hashes(service.store.root)
    with pytest.raises(LiveError,match='最大'): service.estimate_live(plan,fake_card(),[r['id'] for r in plan['reference_images']])
    with pytest.raises(LiveError): _https('fake-image-contract-v1',b'{}')
    assert hashes(service.store.root)==before and not open_budget(service.directory).summary(service.project_id)['project_used_jpy']


@pytest.mark.parametrize('change',['expired','missing_terms','unknown_field','negative','size','url_secret','duplicate_consent','missing_consent','unknown_consent'])
def test_preflight_rejects_bad_cards_or_unapproved_inputs(image_workspace,change):
    values=setup_live(image_workspace); service,plan,card,consents,http=values
    if change=='expired': card['verified_on']=(date.today()-timedelta(days=31)).isoformat()
    elif change=='missing_terms': card['terms_verified']=False
    elif change=='unknown_field': card['api_key']='DO_NOT_STORE'
    elif change=='negative': card['max_input_tokens']=-1
    elif change=='size': card['width']=1536
    elif change=='url_secret': card['source_url']+='?key=DO_NOT_STORE'
    elif change=='duplicate_consent': consents+=consents
    elif change=='missing_consent': consents.clear()
    elif change=='unknown_consent': consents[:]=[str(uuid4())]
    with pytest.raises((ValueError,StorageError)): create_live(values)
    assert not http.calls and not service.store.load()['jobs']


def test_source_hash_or_approval_or_cas_change_blocks_before_sending(image_workspace):
    values=setup_live(image_workspace); service,plan,card,consents,http=values
    quote=service.estimate_live(plan,card,consents)
    with pytest.raises(ValueError): service.create_live_job(plan,card,consents,approval_token='0'*64,confirmed=True,expected_revision=0)
    with pytest.raises(StorageError): service.create_live_job(plan,card,consents,approval_token=quote['approval_token'],confirmed=True,expected_revision=99)
    job,_=create_live(values)
    ref=service.store.root/plan['reference_images'][0]['path']; ref.write_bytes(ref.read_bytes()+b'CHANGED')
    with pytest.raises((ValueError,StorageError)): service.run(job['id'],approved=True)
    assert not http.calls and open_budget(service.directory).request_status(job['id']) is None


@pytest.mark.parametrize('failure',['timeout','http429','http503','missing_usage','bool_usage','negative_usage','wrong_total','unknown_model','unknown_usage','incomplete'])
def test_ambiguous_outcomes_retain_reservation_and_never_resend(image_workspace,failure):
    values=setup_live(image_workspace); service,plan,card,consents,http=values
    if failure=='timeout': http.error=TimeoutError('SENTINEL_SECRET')
    elif failure.startswith('http'): http.error=LiveError(failure,'通信エラー')
    elif failure=='missing_usage': del http.response['usageMetadata']
    elif failure=='bool_usage': http.response['usageMetadata']['promptTokenCount']=True
    elif failure=='negative_usage': http.response['usageMetadata']['thoughtsTokenCount']=-1
    elif failure=='wrong_total': http.response['usageMetadata']['totalTokenCount']=1
    elif failure=='unknown_model': http.response['modelVersion']='unrecognized'
    elif failure=='unknown_usage': http.response['usageMetadata']['unverifiedFeeTokens']=500
    else: http.response=None
    job,quote=create_live(values)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    status=open_budget(service.directory).request_status(job['id'])
    assert status['state']=='uncertain' and status['quote_jpy']==Decimal(quote['max_jpy'])
    assert not service.store.load()['candidates']
    if failure=='missing_usage': assert service.store.load()['jobs'][0]['error_code']=='usage_unknown'
    if failure=='unknown_model': assert service.store.load()['jobs'][0]['error_code']=='model_mismatch'
    with pytest.raises((ValueError,StorageError)): service.run(job['id'],approved=True)
    with pytest.raises((ValueError,StorageError)): service.recover(job['id'],confirmed=True)
    assert len(http.calls)==1
    assert 'SENTINEL_SECRET' not in service.store.path.read_text()


@pytest.mark.parametrize('failure',['zero','multiple','base64','image','dimensions','refusal','unfinished'])
def test_invalid_images_settle_valid_usage_but_never_publish(image_workspace,failure):
    values=setup_live(image_workspace); service,_,_,_,http=values
    parts=http.response['candidates'][0]['content']['parts']
    if failure=='zero': parts.clear()
    elif failure=='multiple': parts.append(copy.deepcopy(parts[0]))
    elif failure=='base64': parts[0]['inlineData']['data']='%%%'
    elif failure=='image': parts[0]['inlineData']['data']=base64.b64encode(b'not image').decode()
    elif failure=='dimensions':
        from PIL import Image
        from io import BytesIO
        stream=BytesIO(); Image.new('RGB',(32,32)).save(stream,'PNG'); parts[0]['inlineData']['data']=base64.b64encode(stream.getvalue()).decode()
    elif failure=='refusal': http.response['candidates'][0]['finishReason']='SAFETY'
    else: http.response['candidates'][0]['finishReason']='MAX_TOKENS'
    job,_=create_live(values)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert open_budget(service.directory).request_status(job['id'])['state']=='settled'
    assert service.store.load()['jobs'][0]['state']=='failed' and not service.store.load()['candidates']
    if failure=='base64': assert service.store.load()['jobs'][0]['error_code']=='invalid_base64'
    if failure=='refusal': assert service.store.load()['jobs'][0]['error_code']=='safety_refusal'
    if failure=='unfinished': assert service.store.load()['jobs'][0]['error_code']=='incomplete_image'
    assert len(http.calls)==1


@pytest.mark.parametrize('point',['response','response_metadata','png','candidate_metadata'])
def test_storage_failure_recovery_never_calls_provider_again(image_workspace,monkeypatch,point):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    original_write=service.store.write; original_save=service.store.save; failed=[]
    def write(path,*args,**kwargs):
        if not failed and ((point=='response' and path.endswith('live_response.json')) or (point=='png' and '/candidates/' in path)):
            failed.append(True); raise OSError('disk failure')
        return original_write(path,*args,**kwargs)
    def save(data,event,**kwargs):
        if not failed and ((point=='response_metadata' and event=='live_response_saved') or (point=='candidate_metadata' and event=='candidate_saved')):
            failed.append(True); raise OSError('disk failure')
        return original_save(data,event,**kwargs)
    monkeypatch.setattr(service.store,'write',write); monkeypatch.setattr(service.store,'save',save)
    with pytest.raises(StorageError): service.run(job['id'],approved=True)
    assert len(http.calls)==1
    ledger=open_budget(service.directory); status=ledger.request_status(job['id'])
    if point in ('response','response_metadata'):
        assert status['state']=='uncertain'
        if point=='response':
            with pytest.raises((ValueError,StorageError)): service.recover(job['id'],confirmed=True)
            return
        ledger.settle(job['id'],actual_jpy='5.9224',confirmed_charge=True)
    restored=service.recover(job['id'],confirmed=True)
    assert restored['id']==job['candidate_id'] and len(http.calls)==1 and len(service.store.load()['candidates'])==1


@pytest.mark.parametrize('scope',['project','monthly'])
def test_shared_budget_blocks_images_including_other_pipeline_spending(image_workspace,scope):
    values=setup_live(image_workspace); service,_,_,_,http=values
    ledger=open_budget(service.directory)
    if scope=='project': ledger.reserve(project_id=service.project_id,request_id='other-voice-spend',provider='aws-polly',model='neural',kind='tts',max_jpy=490)
    else:
        for i in range(10): ledger.reserve(project_id=str(uuid4()),request_id='other-story-'+str(i),provider='openai-responses',model='existing',kind='story',max_jpy=499)
    quote=service.estimate_live(values[1],values[2],values[3])
    assert not quote['budget']['possible']
    with pytest.raises(ValueError): create_live(values)
    assert not http.calls


def test_cannot_switch_ledger_after_image_job(image_workspace,monkeypatch,tmp_path):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    new=tmp_path/'unrelated'/'budget.sqlite3'; monkeypatch.setenv('MANGA_BUDGET_DB',str(new))
    with pytest.raises(ValueError,match='台帳'): service.run(job['id'],approved=True)
    assert not new.exists() and not http.calls


def test_duplicate_request_and_process_lock(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,http=values; job,_=create_live(values)
    duplicate,_=create_live(values); assert duplicate['id']==job['id']
    from manga.image_ai_live_jobs import generation_lock
    with generation_lock(service):
        with pytest.raises(StorageError,match='処理中'): service.run(job['id'],approved=True)
    service.run(job['id'],approved=True); service.run(job['id'],approved=True)
    assert len(http.calls)==1


def test_offline_recovery_cannot_recreate_paid_candidate(image_workspace):
    values=setup_live(image_workspace); service,_,_,_,_=values; job,_=create_live(values)
    from manga.image_ai_jobs import run,recover
    with pytest.raises(ValueError): run(service,job['id'])
    with pytest.raises(ValueError): recover(service,job['id'],confirmed=True)
