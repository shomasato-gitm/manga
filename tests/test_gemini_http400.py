"""Google公式の構造化エラーをfakeで検証。本物の生成APIは呼び出さない。"""
import copy
import io
import json
from urllib.error import HTTPError

import pytest

from manga.budget.config import open_budget
from manga.gemini_diagnostics import Probe,probe_body,preview,run,recover,diagnostic_history,PROVIDER,LIMITS
from manga.gemini_diagnostic_trace import history
from manga.gemini_http_errors import classify_bad_request,MAX_ERROR_BYTES
from story_ai_fixtures import ai_workspace,concepts,job
from test_gemini_diagnostics import prepare,execute,draft,response
from test_story_ai_gemini import setup,next_step

SECRET='PRIVATE-HTTP400-SENTINEL'


def error_body(*,status='INVALID_ARGUMENT',fields=(),details=None):
    value=dict(code=400,status=status,message=SECRET)
    value['details']=details if details is not None else [dict(**{'@type':'type.googleapis.com/google.rpc.BadRequest'},
        fieldViolations=[dict(field=f,description=SECRET) for f in fields])]
    return json.dumps({'error':value,'secret':SECRET}).encode()


@pytest.mark.parametrize('fields,category',[
    (['generationConfig.responseJsonSchema'],'bad_request_json_schema'),
    (['generation_config.response_json_schema.properties.ok'],'bad_request_json_schema'),
    (['generationConfig.responseSchema.properties[0]'],'bad_request_json_schema'),
    (['generation_config.thinking_config.thinking_budget'],'bad_request_thinking'),
    (['generationConfig.responseMimeType'],'bad_request_mime_type'),
    (['generation_config.max_output_tokens'],'bad_request_output_limit'),
    (['contents[0].parts[0].text'],'bad_request_contents'),
    (['generation_config'],'bad_request_generation_config'),
    (['generationConfig.thinkingConfig','generationConfig.responseJsonSchema'],'bad_request_multiple_fields'),
    ([SECRET],'bad_request_invalid_argument'),
    ([],'bad_request_invalid_argument'),
])
def test_bad_request_field_allowlist_never_returns_description(fields,category):
    assert classify_bad_request(error_body(fields=fields))==category


def test_status_and_error_info_are_exact_allowlists():
    assert classify_bad_request(error_body(status='FAILED_PRECONDITION'))=='bad_request_precondition'
    detail={'@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'API_KEY_INVALID','domain':'googleapis.com','metadata':{'apiKey':SECRET}}
    assert classify_bad_request(error_body(details=[detail]))=='bad_request_api_key'
    detail['domain']='unverified.example'
    assert classify_bad_request(error_body(details=[detail]))=='bad_request_invalid_argument'
    assert classify_bad_request(error_body(status=SECRET))=='bad_request'


@pytest.mark.parametrize('raw',[
    SECRET.encode(),b'<html>PRIVATE-HTTP400-SENTINEL</html>',b'[]',b'{"error":null}',
    b'{"error":{"code":400,"status":"INVALID_ARGUMENT","details":false}}',
    b'{"error":{"code":403,"status":"INVALID_ARGUMENT"}}',b'\xff',b'['*10000,
    b'x'*(MAX_ERROR_BYTES+1),
])
def test_unclassified_or_malformed_response_falls_back_without_echo(raw):
    assert classify_bad_request(raw) in ('bad_request','bad_request_invalid_argument')


def test_message_without_structured_field_is_not_used_to_infer_a_cause():
    raw=error_body();value=json.loads(raw);value['error']['message']='Unknown name responseJsonSchema thinkingConfig API_KEY_INVALID '+SECRET
    assert classify_bad_request(json.dumps(value).encode())=='bad_request_invalid_argument'


def test_minimal_probe_keeps_limits_and_fee_controls_separate_from_structured_output():
    body=probe_body()
    assert set(body)=={'contents','generationConfig'}
    assert body['contents']==[{'role':'user','parts':[{'text':'Connection diagnostic only. Return exactly this JSON: {"ok":true}'}]}]
    assert body['generationConfig']=={'candidateCount':1,'maxOutputTokens':256,'thinkingConfig':{'thinkingBudget':0}}
    assert LIMITS==dict(max_input_tokens=1048576,max_output_tokens=256,max_items=0,retry_limit=0)
    assert PROVIDER=='gemini-text'


@pytest.mark.parametrize('category,raw',[
    ('bad_request_json_schema',error_body(fields=['generation_config.response_json_schema'])),
    ('bad_request_thinking',error_body(fields=['generationConfig.thinkingConfig'])),
    ('bad_request_precondition',error_body(status='FAILED_PRECONDITION')),
    ('bad_request_invalid_argument',error_body()),
    ('bad_request',b'x'*(MAX_ERROR_BYTES+1)),
])
def test_transport_classifies_once_without_persisting_raw_error_or_releasing_money(ai_workspace,monkeypatch,capsys,category,raw):
    import manga.story_ai_gemini as module
    service=ai_workspace;prepare(service,monkeypatch);calls=[];reads=[]
    class Body(io.BytesIO):
        def read(self,n=-1):reads.append(n);return super().read(n)
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            raise HTTPError('https://private.invalid/'+SECRET,400,SECRET,{'X-private':SECRET},Body(raw))
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    original=service.stories.path.read_bytes();record=execute(service,Probe())
    assert record['reason']==category and record['state']=='unavailable' and len(calls)==1
    assert reads==[MAX_ERROR_BYTES+1]
    assert any(e['http_status']==400 and e['reason']==category for e in history(service,record['diagnosis_id']))
    book=open_budget(service.directory);status=book.request_status(record['diagnosis_id'])
    assert status['state']=='uncertain' and status['quote_jpy']>0 and book.summary(service.project_id)['blocked']
    with pytest.raises(ValueError):execute(service,Probe())
    with pytest.raises(ValueError):recover(service,record['diagnosis_id'])
    assert len(calls)==1 and service.stories.path.read_bytes()==original
    for path in service.store.root.rglob('*'):
        if path.is_file():assert SECRET.encode() not in path.read_bytes()
    output=capsys.readouterr();assert SECRET not in output.out+output.err


def test_body_read_timeout_keeps_known_http400_without_inventing_field(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as module
    service=ai_workspace;prepare(service,monkeypatch);calls=[]
    class Broken:
        def read(self,n):raise TimeoutError(SECRET)
        def close(self):pass
    class Opener:
        def open(self,*a,**kw):
            calls.append(1);raise HTTPError('https://example.invalid',400,SECRET,{},Broken())
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    record=execute(service,Probe())
    assert record['reason']=='bad_request' and calls==[1]
    assert open_budget(service.directory).request_status(record['diagnosis_id'])['state']=='uncertain'


def test_old_body_approval_cannot_be_reused_and_max_quote_is_unchanged(ai_workspace,monkeypatch):
    import manga.gemini_diagnostics as module
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);current=probe_body
    old=copy.deepcopy(current());old['generationConfig'].update(responseMimeType='application/json',responseJsonSchema=dict(type='object',properties={'ok':{'type':'boolean'}},required=['ok'],additionalProperties=False))
    monkeypatch.setattr(module,'probe_body',lambda _model=None:copy.deepcopy(old));old_plan=preview(service,draft())
    monkeypatch.setattr(module,'probe_body',current);new_plan=preview(service,draft())
    assert old_plan['maximum_jpy']==new_plan['maximum_jpy'] and old_plan['approval_key']!=new_plan['approval_key']
    with pytest.raises(ValueError,match='承認'):run(service,draft(),approval=old_plan['approval_key'],expected_revision=service.store.load()['revision'],probe=probe)
    assert not calls and not diagnostic_history(service)


def test_existing_uncertain_diagnostic_is_not_reclassified_settled_or_deleted(ai_workspace,monkeypatch):
    from manga.story_ai_gemini import GeminiTransportError
    service=ai_workspace
    def reject(*a):raise GeminiTransportError('bad_request')
    probe,calls=prepare(service,monkeypatch,send=reject);record=execute(service,probe)
    before=service.store.path.read_bytes();book=open_budget(service.directory);status=book.request_status(record['diagnosis_id'])
    preview(service,draft())
    with pytest.raises(ValueError):recover(service,record['diagnosis_id'])
    assert service.store.path.read_bytes()==before and book.request_status(record['diagnosis_id'])==status and len(calls)==1


def test_old_saved_response_recovers_after_probe_update_without_resend(ai_workspace,monkeypatch):
    import manga.gemini_diagnostics as module
    service=ai_workspace;probe,calls=prepare(service,monkeypatch);current=probe_body
    old=copy.deepcopy(current());old['generationConfig'].update(responseMimeType='application/json',responseJsonSchema=dict(type='object',properties={'ok':{'type':'boolean'}},required=['ok'],additionalProperties=False))
    monkeypatch.setattr(module,'probe_body',lambda _model=None:copy.deepcopy(old));record=execute(service,probe)
    monkeypatch.setattr(module,'probe_body',current)
    before=open_budget(service.directory).request_status(record['diagnosis_id'])
    assert recover(service,record['diagnosis_id'])['state']=='success' and len(calls)==1
    assert open_budget(service.directory).request_status(record['diagnosis_id'])==before


def test_normal_story_generation_still_uses_json_schema_in_each_separately_approved_call(ai_workspace,monkeypatch):
    service=ai_workspace;calls=setup(service,monkeypatch);original=service.stories.path.read_bytes()
    concepts(service,'gemini-text');created=job(service,'story','gemini-text');service.run_job(created['id'])
    next_step(service,created['id']);next_step(service,created['id'])
    assert len(calls)==4 and service.stories.path.read_bytes()==original and not service.store.load()['adoptions']
    for body in calls:
        config=body['generationConfig']
        assert config['responseMimeType']=='application/json' and config['thinkingConfig']=={'thinkingBudget':0}
        assert 'responseJsonSchema' in config and 'responseSchema' not in config and config['responseJsonSchema']['type']=='object'
    assert set(calls[0]['generationConfig']['responseJsonSchema']['properties'])=={'ideas'}
    assert set(calls[1]['generationConfig']['responseJsonSchema']['properties'])=={'story','page_hooks','change_reason'}
    assert 'criteria' in calls[2]['generationConfig']['responseJsonSchema']['properties']
