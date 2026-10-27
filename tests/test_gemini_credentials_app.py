"""読込比較はJSON・料金・権限の確認前から使える。実通信はしない。"""
import pytest

from manga import gemini_credentials as credentials
from story_ai_fixtures import ai_workspace
from test_story_ai_app import open_studio
from ui_helpers import button,go


@pytest.mark.parametrize('changed',[False,True])
def test_key_comparison_without_saved_pricing_or_permissions(ai_workspace,monkeypatch,changed):
    import manga.budget.config as budget
    import manga.story_ai_gemini as gemini
    first='FAKE-private-original';second='FAKE-private-other'
    monkeypatch.setattr(credentials,'_startup',None)
    monkeypatch.setenv('GEMINI_API_KEY',first);monkeypatch.setenv('GOOGLE_API_KEY',second)
    credentials.capture_startup('before_streamlit')
    if changed:monkeypatch.setenv('GEMINI_API_KEY',second)
    def forbidden(*a,**kw):pytest.fail('no network or budget access')
    monkeypatch.setattr(gemini,'transport',forbidden);monkeypatch.setattr(budget,'open_budget',forbidden)
    before=ai_workspace.store.path.read_bytes();app=open_studio()
    button(app,'キーの読み込み状態を確認する（通信なし）')
    assert not app.exception and not app.error
    assert any('起動時との比較：'+('不一致' if changed else '一致') in m.value for m in app.markdown)
    assert any('Streamlit起動前' in c.value for c in app.caption)
    assert any(('起動後にキー' if changed else 'Google公式SDK') in w.value for w in app.warning)
    assert any('認証・無料枠の利用権限は未確認' in i.value for i in app.info)
    assert first not in str(app) and second not in str(app)
    assert ai_workspace.store.path.read_bytes()==before and ai_workspace.store.load()['card'] is None
    go(app,'企画・ストーリー','ホーム・進行状況');go(app,'企画・ストーリー','AI企画・ストーリー')
    button(app,'キーの読み込み状態を確認する（通信なし）')
    assert any('起動時との比較：'+('不一致' if changed else '一致') in m.value for m in app.markdown)
    assert ai_workspace.store.path.read_bytes()==before


def test_missing_key_never_uses_google_key_or_enables_diagnostic(ai_workspace,monkeypatch):
    from test_gemini35 import card35
    from test_gemini_diagnostics_app import paste
    monkeypatch.setattr(credentials,'_startup',None)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False);monkeypatch.setenv('GOOGLE_API_KEY','FAKE-private-alternate')
    monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1')
    app=open_studio();button(app,'キーの読み込み状態を確認する（通信なし）')
    assert not app.exception and any('GEMINI_API_KEYが未設定' in w.value for w in app.warning)
    paste(app,card35(access=False,free=True))
    next(s for s in app.selectbox if s.label=='初回診断の利用モード').set_value('verified-free').run()
    next(c for c in app.checkbox if c.label=='共有予算の残額を確認して実通信の準備をする').check().run()
    assert next(b for b in app.button if b.label=='承認した初回接続診断を1回だけ実行').disabled
    assert 'FAKE-private-alternate' not in str(app)
