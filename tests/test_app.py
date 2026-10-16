from pathlib import Path

from streamlit.testing.v1 import AppTest

from manga.storage import ProjectStore


APP = Path(__file__).resolve().parents[1] / "app.py"


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def test_create_edit_and_reload_in_new_session(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    app.text_input(key="title").set_value("月の図書館")
    app.text_input(key="genre").set_value("ファンタジー")
    app.text_area(key="synopsis").set_value("司書が失われた物語を探す。")
    app.number_input(key="page_count").set_value(12)
    click(app, "プロジェクトを保存")
    assert app.success
    store = ProjectStore(tmp_path)
    original = store.list_projects()[0][0]
    assert original.page_count == 12

    app.text_input(key="title").set_value("月の図書館 第二稿")
    click(app, "プロジェクトを保存")
    assert store.load(original.id).title == "月の図書館 第二稿"
    assert len(store.list_projects()[0]) == 1

    click(app, "新規プロジェクト")
    assert app.text_input(key="title").value == ""
    assert app.number_input(key="page_count").value == 8

    # セッションを破棄してもディスク上のJSONから再読み込みできる。
    fresh = AppTest.from_file(str(APP)).run()
    click(fresh, "選択したプロジェクトを開く")
    assert fresh.text_input(key="title").value == "月の図書館 第二稿"
    assert fresh.text_area(key="synopsis").value == original.synopsis
    assert fresh.number_input(key="page_count").value == 12


def test_invalid_form_does_not_save(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    app = AppTest.from_file(str(APP)).run()
    click(app, "プロジェクトを保存")
    assert "タイトル" in app.error[0].value
    assert list(tmp_path.glob("*.json")) == []


def test_bad_json_is_visible_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    assert app.warning
