"""Navigation and budget UI smoke tests (no AI API calls)."""
from types import SimpleNamespace
from uuid import uuid4
import pytest

from manga import budget_ui
from manga.navigation import MENUS


@pytest.fixture(autouse=True)
def isolate_user_budget_path(monkeypatch):
    monkeypatch.delenv('MANGA_BUDGET_DB',raising=False)


def test_budget_menu_preserves_existing_top_level_sections():
    assert len(MENUS) == 6
    assert "API予算管理" in MENUS["完成・書き出し"]


def test_budget_screen_requires_a_project(monkeypatch, tmp_path):
    notes = []
    monkeypatch.setattr(budget_ui, "st", SimpleNamespace(info=notes.append))
    budget_ui.render_budget(None, tmp_path)
    assert len(notes) == 1
    assert not (tmp_path / "runtime" / "budget.sqlite3").exists()


def test_budget_screen_uses_shared_runtime_directory(monkeypatch, tmp_path):
    shown = []
    ui = SimpleNamespace(
        session_state={},
        info=shown.append,
        subheader=shown.append,
        caption=shown.append,
        write=shown.append,
        warning=shown.append,
    )
    monkeypatch.setattr(budget_ui, "st", ui)
    work = SimpleNamespace(id=str(uuid4()), title="一話完結テスト")
    budget_ui.render_budget(work, tmp_path / "data" / "projects")
    assert (tmp_path / "data" / "runtime" / "budget.sqlite3").exists()
    assert any("500.00円" in line for line in shown)
