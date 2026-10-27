"""キーを出力せず、環境の不一致をオフラインだけで再現する。"""
import io
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from manga import gemini_credentials as credentials
from manga.budget.config import open_budget
from manga.gemini_diagnostics import offline_report,preview,run,recover,diagnostic_history,probe_body
from manga.gemini_models import DEFAULT_MODEL
from manga.story_ai_gemini import api_key
from story_ai_fixtures import ai_workspace
from test_gemini35 import card35,raw35

FIRST='FAKE-first-private-credential'
SECOND='FAKE-second-private-credential'


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    monkeypatch.setattr(credentials,'_startup',None)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)


@pytest.mark.parametrize('gemini,google,relation',[
    ('','','not_comparable'),(FIRST,'','not_comparable'),('',SECOND,'not_comparable'),
    (FIRST,FIRST,'same'),(FIRST,SECOND,'different')])
def test_explicit_reader_never_falls_back_or_mutates_environment(monkeypatch,gemini,google,relation):
    monkeypatch.setenv('GEMINI_API_KEY',gemini);monkeypatch.setenv('GOOGLE_API_KEY',google)
    report=credentials.credential_report()
    assert api_key is credentials.api_key and report['google_comparison']==relation
    assert report['startup_comparison']=='not_recorded' and report['authentication']=='unverified'
    assert report['network_calls']==report['budget_reservations']==0
    if gemini:assert api_key()==gemini
    else:
        with pytest.raises(ValueError,match='GEMINI_API_KEY'):api_key()
    assert os.environ['GEMINI_API_KEY']==gemini and os.environ['GOOGLE_API_KEY']==google
    assert FIRST not in json.dumps(report) and SECOND not in repr(report)


@pytest.mark.parametrize('value,hint,accepted',[
    ('','',False),(' '+FIRST,'whitespace',False),(FIRST+'\n','control',False),
    ('x'*513,'too_long',False),('"'+FIRST+'"','quote',True),
    ('GEMINI_API_KEY='+FIRST,'assignment',True),('キー'+FIRST,'non_ascii',True)])
def test_format_hints_do_not_silently_repair_or_change_existing_acceptance(monkeypatch,value,hint,accepted):
    monkeypatch.setenv('GEMINI_API_KEY',value)
    report=credentials.credential_report()
    assert report['format_ok'] is accepted
    if hint:assert report['format_hints'][hint]
    assert report['authentication']=='unverified' and os.environ['GEMINI_API_KEY']==value
    if accepted:assert api_key()==value
    else:
        with pytest.raises(ValueError) as error:api_key()
        assert FIRST not in str(error.value)


@pytest.mark.parametrize('original,current',[(FIRST,SECOND),('',FIRST),(FIRST,'')])
def test_startup_is_process_memory_only_and_never_recaptured(monkeypatch,original,current):
    monkeypatch.setenv('GEMINI_API_KEY',original);credentials.capture_startup('before_streamlit')
    monkeypatch.setenv('GEMINI_API_KEY',current);credentials.capture_startup()
    report=credentials.credential_report()
    assert report['startup_comparison']=='changed' and report['startup_stage']=='before_streamlit'
    assert FIRST not in repr(credentials._startup)
    with pytest.raises(TypeError):pickle.dumps(credentials._startup)
    assert FIRST not in json.dumps(report) and SECOND not in json.dumps(report)


def test_streamlit_140_secrets_overwrite_is_reproducible_without_secret_files(monkeypatch):
    from streamlit.runtime.secrets import Secrets
    monkeypatch.setenv('GEMINI_API_KEY',FIRST);credentials.capture_startup('before_streamlit')
    secrets=Secrets()
    monkeypatch.setattr(secrets,'_parse_file_path',lambda path:({'GEMINI_API_KEY':SECOND,'GITHUB_TOKEN':'fake-github'},True))
    monkeypatch.setattr(secrets,'_maybe_install_file_watchers',lambda:None)
    assert secrets.get('GITHUB_TOKEN')=='fake-github'
    assert api_key()==SECOND and credentials.credential_report()['startup_comparison']=='changed'
    # アプリの捕捉だけではStreamlit起動前に起きた変更は遡れない。
    monkeypatch.setattr(credentials,'_startup',None);credentials.capture_startup()
    assert credentials.credential_report()['startup_comparison']=='same'
    assert credentials.credential_report()['startup_stage']=='app_first_run'


def test_dotenv_and_google_key_are_not_loaded_as_fallback(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path);(tmp_path/'.env').write_text('GEMINI_API_KEY='+FIRST)
    monkeypatch.setenv('GOOGLE_API_KEY',SECOND)
    assert not credentials.credential_report()['key_set']
    with pytest.raises(ValueError):api_key()


def test_optional_launcher_captures_before_streamlit_and_preserves_arguments(monkeypatch,capsys):
    from manga import gemini_startup
    from streamlit.web import cli
    monkeypatch.setenv('GEMINI_API_KEY',FIRST);monkeypatch.setattr(sys,'argv',['launcher','--server.port=8510'])
    calls=[]
    def fake_main():
        assert credentials.credential_report()['startup_stage']=='before_streamlit'
        monkeypatch.setenv('GEMINI_API_KEY',SECOND);credentials.capture_startup()
        assert credentials.credential_report()['startup_comparison']=='changed'
        calls.append(list(sys.argv))
    monkeypatch.setattr(cli,'main',fake_main);gemini_startup.main()
    assert len(calls)==1 and calls[0][:2]==['streamlit','run'] and calls[0][-1]=='--server.port=8510'
    assert Path(calls[0][2]).name=='app.py'
    out=capsys.readouterr();assert FIRST not in out.out+out.err and SECOND not in out.out+out.err


def test_terminal_check_outputs_only_flags_and_never_calls_http(monkeypatch):
    environment=dict(os.environ,GEMINI_API_KEY=FIRST,GOOGLE_API_KEY=SECOND)
    result=subprocess.run([sys.executable,'-m','manga.gemini_credentials'],env=environment,capture_output=True,text=True,check=True)
    report=json.loads(result.stdout)
    assert report['google_comparison']=='different' and report['authentication']=='unverified'
    assert report['network_calls']==0 and report['budget_reservations']==0
    assert FIRST not in result.stdout+result.stderr and SECOND not in result.stdout+result.stderr


def test_offline_report_never_writes_or_opens_budget(ai_workspace,monkeypatch):
    import manga.budget.config as budget
    before=ai_workspace.store.path.read_bytes();monkeypatch.setenv('GEMINI_API_KEY',FIRST)
    monkeypatch.setenv('GOOGLE_API_KEY',SECOND);credentials.capture_startup()
    monkeypatch.setattr(budget,'open_budget',lambda *a:pytest.fail('offline must not open ledger'))
    report=offline_report(None,selected_model=DEFAULT_MODEL)
    assert report['credential']['google_comparison']=='different' and report['credential']['startup_comparison']=='same'
    assert report['account_access']==report['free_tier']=='未確認'
    assert ai_workspace.store.path.read_bytes()==before and not diagnostic_history(ai_workspace)


@pytest.mark.parametrize('failure',[False,True])
def test_actual_transport_uses_current_gemini_only_and_retains_existing_guards(ai_workspace,monkeypatch,capsys,failure):
    from urllib.error import HTTPError
    from manga import story_ai_gemini as module
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY',FIRST);credentials.capture_startup('before_streamlit')
    monkeypatch.setenv('GEMINI_API_KEY',SECOND);monkeypatch.setenv('GOOGLE_API_KEY',FIRST)
    monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1');value=card35(access=False,free=True)
    plan=preview(service,value,'verified-free');calls=[]
    class Reply(io.BytesIO):
        status=200
        def read1(self,n):return self.read(n)
    class Opener:
        def open(self,request,timeout):
            assert request.get_header('X-goog-api-key')==SECOND
            assert request.full_url=='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'
            assert request.get_method()=='POST' and '?' not in request.full_url and timeout==30
            body=json.loads(request.data);assert body==probe_body(DEFAULT_MODEL)
            assert body['generationConfig']=={'candidateCount':1,'maxOutputTokens':256,'thinkingConfig':{'thinkingLevel':'minimal'}}
            assert FIRST.encode() not in request.data and SECOND.encode() not in request.data
            calls.append(body)
            if failure:
                error={'error':{'code':400,'status':'INVALID_ARGUMENT','message':SECOND,'details':[
                    {'@type':'type.googleapis.com/google.rpc.ErrorInfo','domain':'googleapis.com','reason':'API_KEY_INVALID','metadata':{'apiKey':SECOND}}]}}
                raise HTTPError(request.full_url,400,SECOND,{},io.BytesIO(json.dumps(error).encode()))
            return Reply(json.dumps(raw35()).encode())
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    args=dict(mode='verified-free',approval=plan['approval_key'],expected_revision=plan['revision'])
    result=run(service,value,**args)
    assert len(calls)==1 and credentials.credential_report()['startup_comparison']=='changed'
    status=open_budget(service.directory).request_status(result['diagnosis_id'])
    assert float(status['quote_jpy'])==pytest.approx(float(plan['maximum_jpy']))
    if failure:
        assert result['reason']=='bad_request_api_key' and status['state']=='uncertain'
        assert open_budget(service.directory).summary(service.project_id)['blocked']
    else:assert result['state']=='success' and status['state']=='settled'
    with pytest.raises(ValueError):run(service,value,**args)
    if failure:
        with pytest.raises(ValueError,match='請求が未確定'):recover(service,result['diagnosis_id'])
        assert open_budget(service.directory).request_status(result['diagnosis_id'])['state']=='uncertain'
    else:recover(service,result['diagnosis_id'])
    assert len(calls)==1
    for path in service.directory.parent.rglob('*'):
        if path.is_file():assert FIRST.encode() not in path.read_bytes() and SECOND.encode() not in path.read_bytes()
    out=capsys.readouterr();assert FIRST not in out.out+out.err and SECOND not in out.out+out.err
