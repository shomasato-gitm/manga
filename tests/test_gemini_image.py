import copy
import hashlib
import io
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from manga.budget.config import open_budget
from manga.gemini_image_contract import MODEL, FLAGS, actual_charge
from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_live_provider import LiveError, GeminiImageAdapter
from manga.storage import StorageError
from image_ai_fixtures import image_workspace
from gemini_image_fixtures import card, service, create, FakeImage


def basis(s):
    person=next(c for c in s.artwork.load()['characters'] if not c['primary_reference_id'])
    return s.plan('character',character_id=person['id'])


def confirm(s, candidate):
    s.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'フェイクのテスト確認。実品質は未検証。',expected_revision=s.store.load()['revision'])
    return s.confirm_lite_first(candidate['id'],confirmed=True,test_only=True)


def test_single_image_links_budget_save_and_first_call_stop(image_workspace):
    s,fake=service(image_workspace); plan=basis(s); before=s.artwork.path.read_bytes()
    job,q=create(s,plan)
    assert q['max_jpy']=='16.051200' and Decimal(q['max_jpy'])*26==Decimal('417.331200')
    assert not fake.calls
    with pytest.raises(ValueError): s.run(job['id'])
    candidate=s.run(job['id'],approved=True)
    assert len(fake.calls)==1 and candidate['model']==MODEL and candidate['mock']
    assert s.artwork.path.read_bytes()==before
    assert s.run(job['id'],approved=True)==candidate
    s.recover(job['id'],confirmed=True)
    assert len(fake.calls)==1
    other=s.plan('panel',page=1,panel=1)
    with pytest.raises(ValueError,match='初回'): create(s,other)
    with pytest.raises(ValueError): s.confirm_lite_first(candidate['id'],confirmed=True)
    confirm(s,candidate)
    scope=s.preview_lite_scope([other],card())
    with pytest.raises(ValueError): s.approve_lite_scope([other],card(),token=scope['token'],expected_revision=s.store.load()['revision'])
    identifier=s.approve_lite_scope([other],card(),token=scope['token'],confirmed=True,expected_revision=s.store.load()['revision'])
    second,_=create(s,other,scope_id=identifier)
    result=s.run(second['id'],approved=True)
    assert len(fake.calls)==2
    assert len(fake.calls[1]['contents'][0]['parts'])>1
    assert all(k not in fake.calls[1] for k in ('tools','cachedContent','serviceTier'))
    assert s.candidate_bytes(result)
    assert open_budget(s.directory).summary(s.project_id)['project_used_jpy']>0


def test_production_transport_fake_http_and_no_secrets(image_workspace,monkeypatch,capsys):
    s,_=service(image_workspace); s._live_adapter=GeminiImageAdapter()
    monkeypatch.setenv('GEMINI_API_KEY','FAKE-PHASE11-SECRET')
    plan=basis(s)
    with pytest.raises(ValueError): create(s,plan)
    monkeypatch.setenv('MANGA_ENABLE_GEMINI_LITE_IMAGE','1')
    calls=[]; fake=FakeImage()
    class Reply(io.BytesIO):
        status=200
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='https://generativelanguage.googleapis.com/v1beta/models/'+MODEL+':generateContent'
            assert request.get_header('X-goog-api-key')=='FAKE-PHASE11-SECRET' and request.get_method()=='POST' and timeout==90
            calls.append(request.data)
            return Reply(json.dumps(fake(MODEL,request.data)).encode())
    monkeypatch.setattr('manga.gemini_image_transport.build_opener',lambda *a:Opener())
    job,_=create(s,plan); result=s.run(job['id'],approved=True)
    assert len(calls)==1 and result['mock'] is False
    with pytest.raises(ValueError): s.confirm_lite_first(result['id'],confirmed=True,test_only=True)
    for path in s.store.root.rglob('*'):
        if path.is_file(): assert b'FAKE-PHASE11-SECRET' not in path.read_bytes()
    output=capsys.readouterr(); assert 'FAKE-PHASE11-SECRET' not in output.out+output.err


@pytest.mark.parametrize('fault',['usage','model','timeout','http400','http403','http404','http429','http503','boolean','extra','tier'])
def test_unknown_results_hold_budget_and_never_retry(image_workspace,fault):
    s,fake=service(image_workspace)
    if fault=='timeout': fake.error=TimeoutError('private')
    elif fault.startswith('http'): fake.error=LiveError(fault,'private')
    elif fault=='usage': fake.mutate=lambda r:r.pop('usageMetadata')
    elif fault=='model': fake.mutate=lambda r:r.update(modelVersion='different')
    elif fault=='boolean': fake.mutate=lambda r:r['usageMetadata'].update(thoughtsTokenCount=True)
    elif fault=='extra': fake.mutate=lambda r:r['usageMetadata'].update(unknown='private')
    elif fault=='tier': fake.mutate=lambda r:r['usageMetadata'].update(serviceTier='PRIORITY')
    job,_=create(s,basis(s))
    with pytest.raises(StorageError): s.run(job['id'],approved=True)
    summary=open_budget(s.directory).summary(s.project_id)
    assert summary['blocked'] and summary['project_reserved_jpy']==Decimal('16.0512')
    for operation in [lambda:s.run(job['id'],approved=True),lambda:s.recover(job['id'],confirmed=True)]:
        with pytest.raises((StorageError,ValueError)): operation()
    assert len(fake.calls)==1 and not s.store.load()['candidates']


@pytest.mark.parametrize('fault',['multiple','aspect','truncated','combined_cap'])
def test_invalid_image_settles_known_usage_but_not_candidate(image_workspace,fault):
    s,fake=service(image_workspace)
    def mutate(r):
        if fault=='multiple': r['candidates'][0]['content']['parts']*=2
        elif fault=='aspect':
            import base64
            from PIL import Image
            stream=io.BytesIO(); Image.new('RGB',(1024,400)).save(stream,'PNG')
            r['candidates'][0]['content']['parts'][0]['inlineData']['data']=base64.b64encode(stream.getvalue()).decode()
        elif fault=='truncated': r['candidates'][0]['finishReason']='MAX_TOKENS'
        else:r['usageMetadata'].update(thoughtsTokenCount=1000,totalTokenCount=2220)
    fake.mutate=mutate; job,_=create(s,basis(s))
    with pytest.raises(StorageError): s.run(job['id'],approved=True)
    assert open_budget(s.directory).request_status(job['id'])['state']=='settled'
    assert not s.store.load()['candidates'] and len(fake.calls)==1


def test_response_saved_before_settlement_failure_recovers_without_http(image_workspace,monkeypatch):
    s,fake=service(image_workspace); job,_=create(s,basis(s))
    from manga.budget import BudgetLedger
    original=BudgetLedger.settle
    monkeypatch.setattr(BudgetLedger,'settle',lambda *a,**k:(_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(StorageError): s.run(job['id'],approved=True)
    ledger=open_budget(s.directory)
    response=json.loads(s.store.read('image_ai/jobs/'+job['id']+'/live_response.json'))['response']
    monkeypatch.setattr(BudgetLedger,'settle',original)
    ledger.settle(job['id'],actual_jpy=actual_charge(card(),response),confirmed_charge=True)
    candidate=s.recover(job['id'],confirmed=True)
    assert candidate['id']==job['candidate_id'] and len(fake.calls)==1


@pytest.mark.parametrize('field',FLAGS)
def test_unconfirmed_terms_permissions_or_maximum_never_allow_execution(image_workspace,field):
    s,fake=service(image_workspace); price=card();price[field]=False
    with pytest.raises(ValueError): create(s,basis(s),price)
    assert not fake.calls and not s.store.load()['jobs']


def test_lower_price_upper_limits_unknown_fields_are_rejected(image_workspace):
    s,fake=service(image_workspace); plan=basis(s)
    for changes in [dict(input_per_million_usd='0'),dict(max_input_tokens=100),dict(max_output_tokens=100000),dict(api_key='secret'),dict(model='wrong')]:
        with pytest.raises(ValueError): create(s,plan,card(**changes))
    assert not fake.calls


def test_fake_first_confirmation_never_unlocks_production_batch(image_workspace):
    s,fake=service(image_workspace); plan=basis(s); job,_=create(s,plan)
    candidate=s.run(job['id'],approved=True); confirm(s,candidate)
    s._live_adapter=GeminiImageAdapter()
    plans=[s.plan('panel',page=1,panel=1),s.plan('character',character_id=plan['character_id'],view='側面')]
    with pytest.raises(ValueError,match='最初'): s.preview_lite_scope(plans,card())
    assert len(fake.calls)==1


def test_scope_refuses_unlisted_target_and_direct_adapter_has_no_send_right(image_workspace):
    s,fake=service(image_workspace); plan=basis(s)
    quote=s.estimate_live(plan,card(),[])
    with pytest.raises(ValueError): s.live_adapter.send(card(),b'{}')
    job,_=create(s,plan); candidate=s.run(job['id'],approved=True); confirm(s,candidate)
    plans=[s.plan('panel',page=1,panel=1)]
    scope=s.preview_lite_scope(plans,card())
    identifier=s.approve_lite_scope(plans,card(),token=scope['token'],confirmed=True,expected_revision=s.store.load()['revision'])
    with pytest.raises(ValueError,match='承認対象外'):
        create(s,s.plan('character',character_id=plan['character_id'],view='側面'),scope_id=identifier)
    assert len(fake.calls)==1
