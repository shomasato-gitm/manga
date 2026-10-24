from datetime import timedelta,date
from decimal import Decimal
from uuid import uuid4

import pytest

from manga.budget.config import open_budget
from manga.story_ai_pricing import evidence
from story_ai_fixtures import ai_workspace,job,concepts,paid,fake_card


def test_paid_requires_flag_and_exact_current_approval(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch);estimate=service.estimate('concepts',provider='openai-responses')
    with pytest.raises(ValueError):service.create_job('concepts',provider='openai-responses',expected_revision=service.store.load()['revision'])
    monkeypatch.delenv('MANGA_ENABLE_PAID_STORY')
    with pytest.raises(ValueError):service.create_job('concepts',provider='openai-responses',expected_revision=service.store.load()['revision'],approval=estimate['approval_key'])
    assert not calls and not service.store.load()['requests']


def test_paid_five_cycles_use_gate_and_same_job_never_pays_twice(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch);concepts(service,'openai-responses')
    created=job(service,'story','openai-responses');result=service.run_job(created['id'])
    assert result['cycle']==5 and len(calls)==13
    ledger=open_budget(service.directory);data=service.store.load()
    assert all(ledger.request_status(r['id'])['state']=='settled' for r in data['requests'])
    used=ledger.summary(service.project_id)['project_used_jpy'];assert 0<used<500
    repeat=job(service,'story','openai-responses');assert repeat['id']==created['id'] and len(calls)==13
    assert ledger.summary(service.project_id)['project_used_jpy']==used


def test_timeout_keeps_reservation_and_never_automatically_resends(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch,fail=True);created=job(service,'concepts','openai-responses')
    result=service.run_job(created['id']);assert result['stop_reason']=='provider_uncertain' and len(calls)==1
    with pytest.raises(ValueError):service.recover(created['id'])
    with pytest.raises(ValueError):job(service,'concepts','openai-responses')
    data=service.store.load();assert 'dummy-sensitive' not in service.store.path.read_text()
    assert open_budget(service.directory).request_status(data['requests'][0]['id'])['state']=='uncertain'


@pytest.mark.parametrize('usage',[{},dict(input_tokens=None,output_tokens=5),dict(input_tokens=True,output_tokens=5)])
def test_unknown_usage_preserves_receipt_for_manual_settlement(ai_workspace,monkeypatch,usage):
    service=ai_workspace;calls=paid(service,monkeypatch,usage=usage);created=job(service,'concepts','openai-responses');service.run_job(created['id'])
    request=service.store.load()['requests'][0];ledger=open_budget(service.directory)
    assert ledger.request_status(request['id'])['state']=='uncertain' and service.store.safe(f"story_ai/responses/{request['id']}.json").is_file()
    ledger.settle(request['id'],actual_jpy='1',confirmed_charge=True)
    service.recover(created['id'])
    assert len(service.store.load()['concept_batches'])==1 and len(calls)==1


def test_500_and_monthly_5000_stop_before_transport(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch);ledger=open_budget(service.directory)
    for i in range(10):ledger.reserve(project_id=service.project_id if i==0 else str(uuid4()),request_id=str(uuid4()),provider='test',model='test',kind='test',max_jpy='500')
    with pytest.raises(ValueError):job(service,'concepts','openai-responses')
    assert not calls and not service.store.load()['jobs']


def test_job_cap_stops_between_calls_despite_global_funds(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch);concepts(service,'openai-responses')
    created=job(service,'story','openai-responses',spend_cap='40.1');result=service.run_job(created['id'])
    assert result['stop_reason']=='budget_limit' and len(calls)==2 and len(service.store.load()['candidates'])==1


@pytest.mark.parametrize('after_commit',[False,True])
def test_settled_metadata_failure_recovers_same_request_without_resend(ai_workspace,monkeypatch,after_commit):
    service=ai_workspace;calls=paid(service,monkeypatch);created=job(service,'concepts','openai-responses');original=service.store.save
    def fail(data,kind,**fields):
        if kind=='response_applied':
            if after_commit:original(data,kind,**fields)
            raise OSError('simulated write failure')
        return original(data,kind,**fields)
    monkeypatch.setattr(service.store,'save',fail);service.run_job(created['id'])
    monkeypatch.setattr(service.store,'save',original);service.recover(created['id'])
    assert len(calls)==1 and len(service.store.load()['concept_batches'])==1


def test_expired_rate_missing_review_and_unknown_fields_reject(ai_workspace):
    service=ai_workspace;card=fake_card();card['verified_on']=(date.today()-timedelta(days=40)).isoformat()
    with pytest.raises(ValueError):service.configure(service.store.load()['settings'],card,expected_revision=service.store.load()['revision'])
    card=fake_card();card['maximum_billing_reviewed']=False
    with pytest.raises(ValueError):evidence(card)
    card=fake_card();card['api_key']='never save'
    with pytest.raises(ValueError):evidence(card)


def test_changed_ledger_cannot_reset_lifetime_budget(ai_workspace,monkeypatch,tmp_path):
    service=ai_workspace;calls=paid(service,monkeypatch);created=job(service,'concepts','openai-responses');service.run_job(created['id'])
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'different.sqlite3'))
    with pytest.raises(ValueError,match='保存先'):job(service,'concepts','openai-responses',new_request=True)
    assert len(calls)==1


def test_invalid_output_is_charged_once_and_never_auto_retried(ai_workspace,monkeypatch):
    def mutate(data,request):data['output'][0]['content'][0]['text']='{broken';return data
    service=ai_workspace;calls=paid(service,monkeypatch,mutate=mutate);created=job(service,'concepts','openai-responses');result=service.run_job(created['id'])
    assert result['stop_reason']=='invalid_output' and len(calls)==1
    request=service.store.load()['requests'][0]
    assert request['state']=='invalid' and open_budget(service.directory).request_status(request['id'])['state']=='settled'


def test_unexpected_response_model_is_not_assumed_to_be_cheap(ai_workspace,monkeypatch):
    def mutate(data,request):data['model']='unknown-expensive-model';return data
    service=ai_workspace;calls=paid(service,monkeypatch,mutate=mutate);created=job(service,'concepts','openai-responses');result=service.run_job(created['id'])
    assert result['stop_reason']=='provider_uncertain' and len(calls)==1
    request=service.store.load()['requests'][0];assert open_budget(service.directory).request_status(request['id'])['state']=='uncertain'


def test_usage_above_verified_max_is_recorded_and_blocks_more_calls(ai_workspace,monkeypatch):
    service=ai_workspace;calls=paid(service,monkeypatch,usage=dict(input_tokens=100000,output_tokens=100000));created=job(service,'concepts','openai-responses');service.run_job(created['id'])
    summary=open_budget(service.directory).summary(service.project_id);assert summary['blocked'] and summary['project_used_jpy']>500
    with pytest.raises(ValueError):job(service,'concepts','openai-responses',new_request=True)
    assert len(calls)==1
