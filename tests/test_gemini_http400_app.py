"""新しい400分類・診断範囲の案内・再送停止を実UIで確認する。"""
import io
from urllib.error import HTTPError

import pytest

from manga.budget.config import open_budget
from manga.gemini_diagnostics import Probe
from story_ai_fixtures import ai_workspace
from test_gemini_diagnostics import prepare,execute,draft
from test_gemini_diagnostics_app import paste
from test_gemini_http400 import error_body,SECRET
from test_story_ai_app import open_studio


@pytest.mark.parametrize('status,fields,word',[
    ('FAILED_PRECONDITION',[],'前提条件'),
    ('INVALID_ARGUMENT',['generationConfig.thinkingConfig'],'思考設定'),
    ('INVALID_ARGUMENT',['generationConfig'],'個別の項目'),
])
def test_400_explanation_without_raw_body_and_no_unapproved_retry(ai_workspace,monkeypatch,status,fields,word):
    import manga.story_ai_gemini as module
    service=ai_workspace;prepare(service,monkeypatch);calls=[]
    class Opener:
        def open(self,*a,**kw):
            calls.append(1);raise HTTPError('https://example.invalid/'+SECRET,400,SECRET,{},io.BytesIO(error_body(status=status,fields=fields)))
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    record=execute(service,Probe());app=open_studio();paste(app,draft())
    next(c for c in app.checkbox if c.label=='共有予算の残額を確認して実通信の準備をする').check().run()
    assert not app.exception and any(word in w.value for w in app.warning)
    assert any('通常の企画・脚本JSONの生成可否は確認しません' in c.value for c in app.caption)
    assert next(b for b in app.button if b.label=='承認した初回接続診断を1回だけ実行').disabled
    assert SECRET not in str(app) and calls==[1]
    assert open_budget(service.directory).request_status(record['diagnosis_id'])['state']=='uncertain'
    assert service.store.load()['card'] is None and not service.store.load()['adoptions']
