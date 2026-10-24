"""Real six-stage Streamlit navigation; only a temporary budget DB is used."""
from pathlib import Path
from uuid import uuid4

from streamlit.testing.v1 import AppTest

from manga.budget import BudgetLedger
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from ui_helpers import go, button

APP = Path(__file__).resolve().parents[1]/'app.py'


def open_app(tmp_path,monkeypatch,project,story):
    directory = tmp_path/'data'/'projects'
    monkeypatch.setenv('MANGA_DATA_DIR',str(directory))
    monkeypatch.delenv('MANGA_BUDGET_DB',raising=False)
    import socket
    def no_network(*a,**kw):raise AssertionError('No external provider calls in UI tests')
    monkeypatch.setattr(socket,'create_connection',no_network)
    ProjectStore(directory).save(project)
    StoryStore(directory,project.id).save(story,expected_version_id=None)
    files = {p:p.read_bytes() for p in directory.rglob('*') if p.is_file() and not p.name.startswith('.')}
    app = AppTest.from_file(str(APP),default_timeout=20).run()
    button(app,'選択したプロジェクトを開く')
    return app,directory,files


def test_pending_counts_remaining_and_micro_yen_are_visible(tmp_path,monkeypatch,manga_project,story):
    app,directory,files = open_app(tmp_path,monkeypatch,manga_project,story)
    book = BudgetLedger(directory.parent/'runtime'/'budget.sqlite3')
    keys = []
    for work in (manga_project.id,str(uuid4())):
        key = str(uuid4());keys.append(key)
        book.reserve(project_id=work,request_id=key,provider='offline',model='fake',kind='tts',max_jpy='0.000001')
    for key in keys:book.mark_uncertain(key)
    go(app,'完成・書き出し','API予算管理')
    texts = '\n'.join(x.value for x in app.markdown)
    assert '0.000001円' in texts and '月間残額' in texts
    assert 'この作品 1件 / 全作品 2件' in texts
    assert any('請求額不明 2件' in w.value for w in app.warning)
    assert not app.exception
    assert all(p.read_bytes()==before for p,before in files.items())


def test_corrupt_ledger_shows_recovery_guidance_and_legacy_screens_still_open(tmp_path,monkeypatch,manga_project,story):
    app,directory,files = open_app(tmp_path,monkeypatch,manga_project,story)
    path = directory.parent/'runtime'/'budget.sqlite3'
    path.parent.mkdir();path.write_bytes(b'invalid sqlite fixture')
    go(app,'完成・書き出し','API予算管理')
    assert not app.exception and any('削除・再作成せず' in e.value for e in app.error)
    assert path.read_bytes()==b'invalid sqlite fixture'
    go(app,'企画・ストーリー','ページ編集')
    assert not app.exception
    assert all(p.read_bytes()==before for p,before in files.items())
