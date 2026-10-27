import json

from manga.gemini_diagnostics import diagnostic_history,Probe
from story_ai_fixtures import ai_workspace
from test_story_ai_app import open_studio
from test_gemini_diagnostics import draft,response
from ui_helpers import button


def paste(app,value):
    if value.get('model') in ('gemini-2.5-flash-lite','gemini-3.5-flash-lite'):
        next(s for s in app.selectbox if s.label=='Geminiモデル（設定・診断）').set_value(value['model']).run()
    next(r for r in app.radio if r.label=='Gemini利用条件・料金JSONの入力方法').set_value('貼り付け').run()
    next(t for t in app.text_area if t.label=='Gemini利用条件・料金JSONを貼り付け').set_value(json.dumps(value)).run()


def test_offline_before_json_save_has_no_network_or_ledger_write(ai_workspace,monkeypatch):
    import manga.gemini_diagnostics as module
    import manga.budget.config as budget
    service=ai_workspace;before=service.store.path.read_bytes()
    monkeypatch.setenv('GEMINI_API_KEY','TEST-PRIVATE-KEY')
    monkeypatch.setattr(module.Probe,'generate',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('no connection')))
    monkeypatch.setattr(budget,'open_budget',lambda *a:(_ for _ in ()).throw(AssertionError('offline no ledger')))
    app=open_studio();button(app,'通信なしで設定を診断する')
    assert not app.exception and not app.error
    assert any('JSONが未入力' in w.value for w in app.warning)
    assert any('設定あり' in x.value for x in app.markdown)
    assert service.store.path.read_bytes()==before
    paste(app,{'model':'gemini-2.5-flash-lite','account_access_reviewed':False})
    button(app,'通信なしで設定を診断する')
    assert not app.exception and any('不足' in w.value for w in app.warning)
    assert 'TEST-PRIVATE-KEY' not in str(app)


def test_valid_draft_is_diagnosable_without_fake_access_confirmation(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as gemini
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY','TEST-PRIVATE-KEY');monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1');calls=[]
    def fake(*a):calls.append(a);return response()
    monkeypatch.setattr(gemini,'transport',fake)
    app=open_studio();paste(app,draft());button(app,'通信なしで設定を診断する')
    assert any('モデル利用権限：未確認' in m.value for m in app.markdown) and not calls
    assert next(b for b in app.button if b.label=='承認した初回接続診断を1回だけ実行').disabled
    next(c for c in app.checkbox if c.label=='共有予算の残額を確認して実通信の準備をする').check().run()
    next(c for c in app.checkbox if c.label=='料金・利用条件・送信内容・最大費用を確認し、この固定文の診断を1回だけ承認する').check().run()
    assert not calls
    button(app,'承認した初回接続診断を1回だけ実行')
    assert not app.exception and not app.error and len(calls)==1
    assert diagnostic_history(service)[-1]['state']=='success'
    assert any('無料枠の適用' in s.value for s in app.success)
    assert service.store.load()['card'] is None and not service.store.load()['candidates']
    assert not next(c for c in app.checkbox if c.label=='料金・利用条件・送信内容・最大費用を確認し、この固定文の診断を1回だけ承認する').value
    assert 'TEST-PRIVATE-KEY' not in str(app)


def test_failed_diagnostic_is_not_success_and_shows_action(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as gemini
    from manga.story_ai_gemini import GeminiTransportError
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY','TEST-PRIVATE-KEY');monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1')
    def forbidden(*a):raise GeminiTransportError('forbidden')
    monkeypatch.setattr(gemini,'transport',forbidden)
    app=open_studio();paste(app,draft())
    next(c for c in app.checkbox if c.label=='共有予算の残額を確認して実通信の準備をする').check().run()
    next(c for c in app.checkbox if c.label=='料金・利用条件・送信内容・最大費用を確認し、この固定文の診断を1回だけ承認する').check().run()
    button(app,'承認した初回接続診断を1回だけ実行')
    assert not app.exception
    assert any('403' in w.value and 'プロジェクト' in w.value for w in app.warning)
    assert any('利用不可' in x.value for x in app.markdown)
    assert not any('有効な応答' in s.value for s in app.success)
    assert service.store.load()['card'] is None


def test_selected_history_does_not_show_another_diagnosis_success(ai_workspace,monkeypatch):
    from test_gemini_diagnostics import prepare,execute
    from manga.story_ai_gemini import GeminiTransportError
    from manga.budget.config import open_budget
    service=ai_workspace
    def forbidden(*a):raise GeminiTransportError('forbidden')
    probe,_=prepare(service,monkeypatch,send=forbidden);first=execute(service,probe)
    open_budget(service.directory).reconcile_no_charge(first['diagnosis_id'],confirmed_no_charge=True)
    good,_=prepare(service,monkeypatch);execute(service,good)
    app=open_studio();assert any('有効な応答' in s.value for s in app.success)
    next(s for s in app.selectbox if s.label=='請求・保存を照合する診断').set_value(first['diagnosis_id']).run()
    assert any('403' in w.value for w in app.warning)
    assert not any('有効な応答' in s.value for s in app.success)
