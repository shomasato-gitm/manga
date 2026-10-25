"""Phase9診断のテスト。生成・キー検証・localhostを含むネットワークを禁止する。"""
import copy
import json
import socket
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from manga.api_architecture_diagnostics import EVIDENCE, assess_evidence, diagnostic, voicevox_origin_allowed

TODAY = date(2026,10,26)


@pytest.fixture
def evidence():
    return json.loads(EVIDENCE.read_text())


def test_offline_diagnostic_does_not_connect_write_or_expose_secrets(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('診断がネットワーク/予算/保存にアクセスした')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    from manga.budget import BudgetLedger
    monkeypatch.setattr(BudgetLedger,'__init__',forbidden)
    monkeypatch.setattr(Path,'write_text',forbidden)
    monkeypatch.setattr(Path,'write_bytes',forbidden)
    monkeypatch.chdir(tmp_path)
    secret='FAKE_SECRET_DO_NOT_EXPOSE_987654321'
    env={k:secret for k in ('OPENAI_API_KEY','GEMINI_API_KEY','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY')}
    env.update({k:'1' for k in ('MANGA_ENABLE_PAID_STORY','MANGA_ENABLE_PAID_TTS','MANGA_ENABLE_IMAGE_API')})
    report=diagnostic(environ=env,today=TODAY)
    assert report['credential_presence']==dict(openai=True,gemini=True,aws=True)
    assert all(report['existing_flags'].values())
    assert report['existing_gemini_network_verified'] is False
    assert not report['phase9a_execution_enabled'] and not report['network_calls'] and not report['budget_reservations']
    assert report['budget']['remaining_jpy'] is None
    assert report['budget']['project_lifetime_limit_jpy']==500
    assert report['budget']['all_projects_monthly_limit_jpy']==5000
    assert secret not in json.dumps(report)
    assert not list(tmp_path.iterdir())


def test_all_providers_remain_blocked_even_when_json_claims_everything_confirmed(evidence):
    for row in evidence['providers']:
        row['execution_requirements']={k:'confirmed' for k in row['execution_requirements']}
        row['maximum_jpy']='1'
        row['execution_approved']=True
    report=assess_evidence(evidence,today=TODAY)
    assert len(report['providers'])==10
    assert all(not r['phase9a_execution_enabled'] and r['maximum_jpy'] is None for r in report['providers'])


def test_partial_prices_and_missing_official_fetch_never_become_execution_quote(evidence):
    evidence['sources'][0]['retrieved']=False
    report=assess_evidence(evidence,today=TODAY)
    google=next(r for r in report['providers'] if r['provider']=='gemini-image')
    assert 'official-source-unavailable' in google['blockers']
    assert 'maximum-charge-unknown' in google['blockers']
    assert 'maximum_charge' in google['blockers']
    from manga.story_ai_pricing import evidence as execution_card
    with pytest.raises(ValueError):execution_card(evidence)
    from manga.image_ai_live_pricing import validate_card
    with pytest.raises(ValueError):validate_card(evidence)


@pytest.mark.parametrize('day',[date(2026,10,25),date(2026,11,26)])
def test_future_or_expired_evidence_is_reported_as_blocked(evidence,day):
    report=assess_evidence(evidence,today=day)
    assert report['fresh'] is False
    assert all('evidence-expired-or-future' in r['blockers'] for r in report['providers'])


def test_default_date_uses_japan_day_at_utc_boundary(evidence,monkeypatch):
    import manga.api_architecture_diagnostics as module
    from datetime import datetime, timedelta, timezone
    class Clock:
        @staticmethod
        def now(tz):
            assert tz.utcoffset(None)==timedelta(hours=9)
            return datetime(2026,10,25,15,1,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(module,'datetime',Clock)
    assert assess_evidence(evidence)['fresh'] is True


@pytest.mark.parametrize('change',['missing-provider','missing-requirement','duplicate-source','unknown-source','bad-schema','unsafe-url'])
def test_malformed_or_untrusted_evidence_is_rejected_without_echo(evidence,change):
    secret='UNTRUSTED_SECRET_ABC'
    if change=='missing-provider':evidence['providers'].pop()
    if change=='missing-requirement':evidence['providers'][0]['execution_requirements'].pop('maximum_charge')
    if change=='duplicate-source':evidence['sources'].append(copy.deepcopy(evidence['sources'][0]))
    if change=='unknown-source':evidence['providers'][0]['facts']['pricing']['source_ids']=[secret]
    if change=='bad-schema':evidence['schema_version']=True
    if change=='unsafe-url':evidence['sources'][0]['url']='https://ai.google.dev.evil.test/?key='+secret
    with pytest.raises(ValueError) as error:assess_evidence(evidence,today=TODAY)
    assert secret not in str(error.value)


@pytest.mark.parametrize('origin',[
 'http://127.0.0.1:50021','http://[::1]:50021',
])
def test_explicit_loopback_origins_are_only_policy_candidates(origin):
    assert voicevox_origin_allowed(origin)


@pytest.mark.parametrize('origin',[
 'https://127.0.0.1:50021','http://localhost:50021','http://127.0.0.2:50021',
 'http://127.0.0.1:80','http://127.0.0.1:50021/','http://127.0.0.1:50021/audio_query',
 'http://user:pass@127.0.0.1:50021','http://127.0.0.1:50021?redirect=https://example.com',
 'http://127.0.0.1:50021#fragment','http://127.0.0.1.evil.test:50021',
 'http://2130706433:50021','http://0177.0.0.1:50021','http://0x7f000001:50021',
 'http://[::ffff:127.0.0.1]:50021','http://[::1%25lo]:50021',
 'http://169.254.169.254:50021','http://192.168.1.1:50021',
 'http://example.com:50021',' http://127.0.0.1:50021',None,
])
def test_url_spoofs_remote_addresses_and_redirect_origins_are_rejected(origin):
    assert not voicevox_origin_allowed(origin)


def test_existing_image_guards_and_common_budget_limits_are_still_enforced(monkeypatch,tmp_path):
    from manga.image_ai_live_provider import network_allowed,LiveError
    monkeypatch.setenv('MANGA_ENABLE_IMAGE_API','1')
    monkeypatch.setenv('GEMINI_API_KEY','FAKE_TEST_KEY')
    with pytest.raises(LiveError):network_allowed()
    # 診断後も既存の確定フラグを解除していない。
    from manga.image_ai_live_pricing import NETWORK_VERIFIED,research
    assert NETWORK_VERIFIED is False and research()['network_enabled'] is False
    from manga.budget.config import open_budget
    ledger=open_budget(tmp_path/'data'/'projects')
    from uuid import uuid4
    summary=ledger.summary(str(uuid4()))
    assert summary['project_remaining_jpy']==500 and summary['monthly_remaining_jpy']==5000


def test_cli_error_is_sanitized_and_reports_no_execution(monkeypatch,capsys):
    from scripts import diagnose_api_safety as cli
    monkeypatch.setattr(cli,'diagnostic',Mock(side_effect=OSError('FAKE_CREDENTIAL_IN_EXCEPTION')))
    assert cli.main()==2
    result=json.loads(capsys.readouterr().out)
    assert 'FAKE_CREDENTIAL' not in str(result)
    assert result['phase9a_execution_enabled'] is False and result['network_calls']==0
