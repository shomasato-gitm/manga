"""成功時の実キーは使わず、最小REST参照形式と実HTTPコードを比較する。"""
import json
import os
from uuid import uuid4
from urllib.request import Request,build_opener

import pytest

from manga import gemini_credentials as credentials
from manga.budget.config import open_budget
from manga.budget.guard import current_execution
from manga.gemini_diagnostics import preview,run,recover,diagnostic_history,probe_body
from manga.gemini_http_errors import classify_bad_request
from manga.story_ai_provider import NoRedirect
from story_ai_fixtures import ai_workspace
from gemini_wire_fakes import wire
from test_gemini35 import card35,raw35

KEY='FAKE-OFFLINE-REST-CREDENTIAL'
OTHER='FAKE-SECOND-REST-CREDENTIAL'
URL='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent'


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY',KEY)
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)
    monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    monkeypatch.setattr(credentials,'_startup',None)
    credentials.capture_startup('before_streamlit')


def execute(service):
    card=card35(access=False,free=True);plan=preview(service,card,'verified-free')
    return run(service,card,mode='verified-free',approval=plan['approval_key'],expected_revision=plan['revision'])


def minimal_reference(variant):
    # 成功時のメソッド・認証・生成設定だけを本人報告に合わせる。
    # contentsは未提供なので同じ固定文を仮に使用。実際の本文・キーの復元ではない。
    # すべてオフライン専用。実送信の予算・承認契約へ流用しない。
    query=variant=='official-minimal-query'
    body={'contents':[{'role':'user','parts':[{'text':'Connection diagnostic only. Reply with exactly OK.'}]}]}
    if variant=='reported-success':body['generationConfig']={'maxOutputTokens':64}
    headers={'Content-Type':'application/json'}
    if not query:headers['x-goog-api-key']=KEY
    request=Request(URL+('?key='+KEY if query else ''),data=json.dumps(body).encode(),headers=headers,method='POST')
    with build_opener(NoRedirect()).open(request,timeout=30) as response:response.read()


@pytest.mark.parametrize('variant',['reported-success','official-minimal-header','official-minimal-query'])
def test_minimal_rest_comparison_report_is_redacted_and_offline(ai_workspace,wire,variant,capsys):
    query=variant=='official-minimal-query'
    wire.response=raw35();minimal_reference(variant)
    reference=wire.safe_summary();result=execute(ai_workspace);studio=wire.safe_summary()
    assert result['state']=='success' and len(wire.connections)==2
    assert reference['post'] and reference['http11'] and reference['path_matches'] and reference['content_type_json']
    assert reference['auth_header_count']==(0 if query else 1) and reference['query_key_present'] is query
    assert studio==dict(post=True,http11=True,path_matches=True,auth_header_count=1,query_key_present=False,
        content_type_json=True,role_user=True,candidate_count_1_explicit=True,max_output_256_explicit=True,max_output_64_explicit=False,thinking_minimal_explicit=True,
        structured_output_present=False,timeout_30=True)
    method,target,version,headers,body=wire.request()
    assert headers['x-goog-api-key']==[KEY.encode()] and headers['host']==[b'generativelanguage.googleapis.com']
    assert 'authorization' not in headers and KEY not in target and KEY not in json.dumps(body)
    assert body==probe_body('gemini-3.5-flash-lite')
    # -s指定でMacでも閲覧可能。比較の基準と状態以外を出力しない。
    if variant=='reported-success':
        ref_method,ref_target,ref_version,ref_headers,ref_body=wire.request(0)
        assert (ref_method,ref_target,ref_version)==(method,target,version)
        assert ref_body['generationConfig']=={'maxOutputTokens':64}
        assert ref_body['contents']==body['contents']  # 未提供の本文を共通にし、既知の設定差だけを比較。
        assert ref_headers['x-goog-api-key']==headers['x-goog-api-key']
        assert {k:v for k,v in ref_headers.items() if k!='content-length'}=={k:v for k,v in headers.items() if k!='content-length'}
        assert {k for k in studio if studio[k]!=reference[k]}=={
            'candidate_count_1_explicit','max_output_256_explicit','max_output_64_explicit','thinking_minimal_explicit'}
    report=dict(reference_kind=variant+'-synthetic-contents-and-key-not-real-capture',real_network_calls=0,
                key_identity_with_past_success='unverified',reference=reference,studio=studio,
                different_fields=[k for k in studio if studio[k]!=reference[k]])
    assert KEY not in json.dumps(report) and OTHER not in json.dumps(report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    output=capsys.readouterr();assert KEY not in output.out+output.err and OTHER not in output.out+output.err
    # capsysを使う他テストと同様に秘密の非出力を確認した後、安全な概要だけを戻す。
    print(output.out,end='')


def test_key_is_read_at_preflight_and_again_inside_once_only_execution(ai_workspace,wire,monkeypatch):
    import manga.gemini_diagnostics as module
    before=ai_workspace.stories.path.read_bytes();stages=[];reader=module.api_key
    def observed():
        stages.append('sending' if current_execution() else 'preflight')
        return reader()
    monkeypatch.setattr(module,'api_key',observed);wire.response=raw35()
    assert credentials.credential_report()['startup_comparison']=='same'
    result=execute(ai_workspace)
    assert stages==['preflight','sending']
    assert wire.request()[3]['x-goog-api-key']==[KEY.encode()]
    assert credentials.credential_report()['startup_comparison']=='same'
    assert ai_workspace.stories.path.read_bytes()==before and not ai_workspace.store.load()['adoptions']
    assert open_budget(ai_workspace.directory).request_status(result['diagnosis_id'])['state']=='settled'


def test_startup_check_does_not_prove_key_identity_at_a_later_send(ai_workspace,wire,monkeypatch):
    from manga.gemini_diagnostic_trace import Trace
    assert credentials.credential_report()['startup_comparison']=='same'
    original=Trace.record
    def change_after_preflight(self,stage,outcome,**kwargs):
        if (stage,outcome)==('pre_send','started'):monkeypatch.setenv('GEMINI_API_KEY',OTHER)
        return original(self,stage,outcome,**kwargs)
    monkeypatch.setattr(Trace,'record',change_after_preflight);wire.response=raw35()
    result=execute(ai_workspace)
    assert result['state']=='success' and len(wire.connections)==1
    assert wire.request()[3]['x-goog-api-key']==[OTHER.encode()]
    assert credentials.credential_report()['startup_comparison']=='changed'
    # 仮想的な変更の再現であり、Macで変更が起きた証拠とは扱わない。
    assert os.environ['GEMINI_API_KEY']==OTHER


def error_info(**changes):
    detail={'@type':'type.googleapis.com/google.rpc.ErrorInfo','domain':'googleapis.com','reason':'API_KEY_INVALID'}
    detail.update(changes);return detail


@pytest.mark.parametrize('detail,expected',[
    (error_info(),'bad_request_api_key'),
    (error_info(reason='API_KEY_EXPIRED'),'bad_request_invalid_argument'),
    (error_info(domain='not-google.invalid'),'bad_request_invalid_argument'),
    (error_info(**{'@type':'google.rpc.ErrorInfo'}),'bad_request_invalid_argument'),
    ({'@type':'type.googleapis.com/google.rpc.LocalizedMessage','message':'API_KEY_INVALID'},'bad_request_invalid_argument'),
    ({'@type':'type.googleapis.com/google.rpc.BadRequest','fieldViolations':[{'field':'generationConfig.thinkingConfig.thinkingLevel'}]},'bad_request_thinking'),
    ({'@type':'type.googleapis.com/google.rpc.BadRequest','fieldViolations':[{'field':'generationConfig.maxOutputTokens'}]},'bad_request_output_limit'),
    ({'@type':'type.googleapis.com/google.rpc.BadRequest','fieldViolations':[{'field':'generationConfig.candidateCount'}]},'bad_request_generation_config'),
])
def test_structured_auth_error_is_not_inferred_from_text_or_generation_options(detail,expected):
    payload={'error':{'code':400,'status':'INVALID_ARGUMENT','message':'API_KEY_INVALID '+KEY,'details':[detail]}}
    assert classify_bad_request(json.dumps(payload).encode())==expected


@pytest.mark.parametrize('status,category',[(400,'bad_request_api_key'),(401,'unauthorized'),(403,'forbidden'),(404,'not_found'),(429,'rate_limited'),(500,'server_error')])
def test_actual_http_response_classification_and_no_resend(ai_workspace,wire,status,category,capsys):
    wire.status=status;wire.response={'error':{'code':status,'status':'INVALID_ARGUMENT','message':KEY,
        'details':[dict(error_info(),metadata={'apiKey':KEY})]}}
    result=execute(ai_workspace);assert result['reason']==category and len(wire.connections)==1
    ledger=open_budget(ai_workspace.directory);before=ledger.request_status(result['diagnosis_id'])
    assert before['state']=='uncertain' and ledger.summary(ai_workspace.project_id)['blocked']
    with pytest.raises(ValueError):execute(ai_workspace)
    with pytest.raises(ValueError,match='請求が未確定'):recover(ai_workspace,result['diagnosis_id'])
    assert ledger.request_status(result['diagnosis_id'])==before and len(wire.connections)==1
    assert diagnostic_history(ai_workspace)[-1]['reason']==category
    for path in ai_workspace.directory.parent.rglob('*'):
        if path.is_file():assert KEY.encode() not in path.read_bytes() and OTHER.encode() not in path.read_bytes()
    out=capsys.readouterr();assert KEY not in out.out+out.err


def test_timeout_keeps_original_maximum_without_a_retry(ai_workspace,wire,monkeypatch):
    calls=[]
    def fail_read(*args,**kwargs):
        calls.append('connect')
        raise TimeoutError('not logged '+KEY)
    monkeypatch.setattr(wire,'connect',fail_read)
    result=execute(ai_workspace)
    assert result['reason']=='connection_timeout'
    status=open_budget(ai_workspace.directory).request_status(result['diagnosis_id'])
    assert status['state']=='uncertain' and status['quote_jpy']>0
    with pytest.raises(ValueError):execute(ai_workspace)
    assert calls==['connect']


def test_missing_or_reused_individual_approval_never_reaches_http(ai_workspace,wire):
    value=card35(access=False,free=True);plan=preview(ai_workspace,value,'verified-free');wire.response=raw35()
    args=dict(mode='verified-free',expected_revision=plan['revision'])
    for approval in (None,'wrong-approval'):
        with pytest.raises(ValueError,match='承認'):run(ai_workspace,value,approval=approval,**args)
    assert not wire.connections and not diagnostic_history(ai_workspace)
    result=run(ai_workspace,value,approval=plan['approval_key'],**args)
    with pytest.raises(ValueError):run(ai_workspace,value,approval=plan['approval_key'],**args)
    assert result['state']=='success' and len(wire.connections)==1


@pytest.mark.parametrize('block',['project','monthly','uncertain'])
def test_common_budget_or_unknown_bill_stops_before_http(ai_workspace,wire,block):
    ledger=open_budget(ai_workspace.directory);identifiers=[]
    for _ in range(10 if block=='monthly' else 1):
        identifier=str(uuid4());identifiers.append(identifier)
        ledger.reserve(project_id=str(uuid4()) if block=='monthly' else ai_workspace.project_id,
            request_id=identifier,provider='gemini-text',model='gemini-2.5-flash-lite',kind='existing',
            max_jpy='23.8128' if block=='uncertain' else '500')
        if block=='uncertain':ledger.mark_uncertain(identifier)
    before=[ledger.request_status(i) for i in identifiers]
    with pytest.raises(ValueError):execute(ai_workspace)
    assert not wire.connections and not diagnostic_history(ai_workspace)
    assert [ledger.request_status(i) for i in identifiers]==before
