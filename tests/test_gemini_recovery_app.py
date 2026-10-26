"""実UIの未確定明細・個別照合・復旧。実API、既存ユーザーデータは使わない。"""
from manga.budget.config import open_budget
from manga.gemini_diagnostics import diagnostic_history
from story_ai_fixtures import ai_workspace
from test_gemini_diagnostics import prepare,execute
from test_gemini_recovery import pending
from test_story_ai_app import open_studio
from ui_helpers import button,go


def failed(service,monkeypatch):
    def timeout(*a):raise TimeoutError('TEST-SECRET-NEVER-PERSIST')
    probe,calls=prepare(service,monkeypatch,send=timeout)
    return execute(service,probe),calls


def choose(app,outcome):
    next(s for s in app.selectbox if s.label=='提供元で確認できた請求結果').set_value(outcome).run()


def confirm(app):
    next(c for c in app.checkbox if c.label.startswith('提供元の確定した請求結果を確認')).check().run()
    next(c for c in app.checkbox if c.label.startswith('作品・リクエストID・金額')).check().run()


def test_no_charge_is_explicit_and_then_diagnostic_recovers_without_resend(ai_workspace,monkeypatch):
    service=ai_workspace;result,calls=failed(service,monkeypatch);book=open_budget(service.directory)
    before=service.stories.path.read_bytes();app=open_studio()
    button(app,'請求未確定の明細・手動照合へ')
    assert not app.exception and any(result['diagnosis_id'] in str(d.value) for d in app.dataframe)
    assert any('データなし' in w.value for w in app.warning)
    assert book.request_status(result['diagnosis_id'])['state']=='uncertain'
    choose(app,'無課金が確定')
    apply=next(b for b in app.button if b.label=='確認済みの請求結果をこの1件に反映（再送なし）')
    assert apply.disabled
    confirm(app);button(app,apply.label)
    assert not app.error and not app.exception and book.request_status(result['diagnosis_id'])['state']=='settled'
    assert any('無課金確認' in d.value['照合結果'].tolist() for d in app.dataframe if '照合結果' in d.value.columns)
    button(app,'Gemini初回診断の保存・請求照合へ戻る')
    button(app,'診断の保存・請求を照合する（再送なし）')
    assert not app.error and not app.exception and len(calls)==1
    assert diagnostic_history(service)[0]['state']=='unavailable'
    assert service.stories.path.read_bytes()==before and service.store.load()['card'] is None
    assert 'TEST-SECRET-NEVER-PERSIST' not in str(app)


def test_charge_amount_change_requires_fresh_consent_and_preserves_other_pending(ai_workspace,monkeypatch):
    service=ai_workspace;book=open_budget(service.directory);work,other=pending(book,uncertain=False)
    result,calls=failed(service,monkeypatch);book.mark_uncertain(other)
    app=open_studio();go(app,'完成・書き出し','API予算管理');choose(app,'請求額が確定')
    amount=next(t for t in app.text_input if t.label=='このリクエストの確定請求額（円）')
    amount.set_value('NaN').run();confirm(app);button(app,'確認済みの請求結果をこの1件に反映（再送なし）')
    assert app.error and book.request_status(result['diagnosis_id'])['state']=='uncertain'
    next(t for t in app.text_input if t.label=='このリクエストの確定請求額（円）').set_value('0.12').run()
    assert not next(c for c in app.checkbox if c.label.startswith('作品・リクエストID・金額')).value
    confirm(app);button(app,'確認済みの請求結果をこの1件に反映（再送なし）')
    assert not app.exception and not app.error and str(book.request_status(result['diagnosis_id'])['actual_jpy'])=='0.12'
    assert book.request_status(other)['state']=='uncertain' and len(book.reconciliation_history())==1
    assert book.summary(service.project_id)['blocked'] and len(calls)==1


def test_switching_target_does_not_reuse_another_confirmation(ai_workspace,monkeypatch):
    service=ai_workspace;book=open_budget(service.directory);_,other=pending(book,uncertain=False)
    result,calls=failed(service,monkeypatch);book.mark_uncertain(other)
    app=open_studio();go(app,'完成・書き出し','API予算管理');choose(app,'無課金が確定');confirm(app)
    next(s for s in app.selectbox if s.label=='照合する未確定リクエスト').set_value(other).run()
    assert next(s for s in app.selectbox if s.label=='提供元で確認できた請求結果').value=='未確認・反映待ち'
    next(s for s in app.selectbox if s.label=='照合する未確定リクエスト').set_value(result['diagnosis_id']).run()
    choose(app,'無課金が確定')
    assert not next(c for c in app.checkbox if c.label.startswith('作品・リクエストID・金額')).value
    assert all(r['state']=='uncertain' for r in book.pending_requests()) and len(calls)==1


def test_returning_to_previous_amount_needs_new_confirmation(ai_workspace,monkeypatch):
    service=ai_workspace;result,calls=failed(service,monkeypatch)
    app=open_studio();go(app,'完成・書き出し','API予算管理');choose(app,'請求額が確定')
    next(t for t in app.text_input if t.label=='このリクエストの確定請求額（円）').set_value('0.12').run();confirm(app)
    next(t for t in app.text_input if t.label=='このリクエストの確定請求額（円）').set_value('0.2').run()
    next(t for t in app.text_input if t.label=='このリクエストの確定請求額（円）').set_value('0.12').run()
    assert not next(c for c in app.checkbox if c.label.startswith('提供元の確定した請求結果を確認')).value
    assert open_budget(service.directory).request_status(result['diagnosis_id'])['state']=='uncertain' and len(calls)==1


def test_legacy_record_has_no_invented_phase_and_view_does_not_reconcile(ai_workspace,monkeypatch):
    service=ai_workspace;result,calls=failed(service,monkeypatch)
    directory=service.store.safe(f"story_ai/diagnostics/{result['diagnosis_id']}.trace")
    for path in directory.iterdir():path.unlink()
    directory.rmdir()
    book=open_budget(service.directory);before=book.request_status(result['diagnosis_id']);metadata=service.store.path.read_bytes()
    app=open_studio()
    assert any('旧記録' in i.value and '特定できません' in i.value for i in app.info)
    assert not app.exception and book.request_status(result['diagnosis_id'])==before
    assert service.store.path.read_bytes()==metadata and len(calls)==1


def test_reserved_request_cannot_be_released_from_manual_ui(ai_workspace):
    from uuid import uuid4
    service=ai_workspace;book=open_budget(service.directory);identifier=str(uuid4())
    book.reserve(project_id=service.project_id,request_id=identifier,provider='offline',model='fake',kind='test',max_jpy='23.8128')
    app=open_studio();go(app,'完成・書き出し','API予算管理')
    assert any('実行中の可能性' in i.value for i in app.info)
    assert not any(s.label=='提供元で確認できた請求結果' for s in app.selectbox)
    assert book.request_status(identifier)['state']=='reserved' and not app.exception
