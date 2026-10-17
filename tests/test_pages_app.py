"""ページ編集→保存→プレビュー→出力→再起動を外部通信なしで実行する。"""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from manga.page_storage import PageStore
from manga.storage import ProjectStore
from manga.story_storage import StoryStore


APP = Path(__file__).resolve().parents[1] / "app.py"


from ui_helpers import element, button, go


@pytest.fixture
def workspace(tmp_path, monkeypatch, manga_project, story):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("ページ編集・出力で外部へ通信してはいけません。")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    app = AppTest.from_file(str(APP)).run()
    button(app, "選択したプロジェクトを開く")
    return app, PageStore(tmp_path, manga_project.id)


def test_edit_preview_export_and_reopen(workspace):
    app, store = workspace
    assert not store.path.exists()  # 開くだけでは既存データを書き換えない
    element(app, "selectbox", "ページの横幅（px）").set_value(600)
    button(app, "ページ共通設定を保存")
    element(app, "selectbox", "レイアウトテンプレート").set_value("grid").run()
    button(app, "このページのコマ割りを保存")
    assert store.load()["pages"]["1"]["template"] == "grid"
    element(app, "checkbox", "この吹き出し専用の文章を使う").check().run()
    element(app, "text_area", "吹き出しの本文").set_value("月へ！")
    element(app, "selectbox", "文字の方向").set_value("horizontal")
    button(app, "吹き出しを保存")
    assert store.load()["pages"]["1"]["bubbles"][0]["text"] == "月へ！"
    button(app, "ページをプレビュー・再描画")
    assert not app.error
    button(app, "出力ファイルを生成")
    export_key = f"composition_{store.project_id}_export"
    export = app.session_state[export_key]
    assert export["filename"] == "page_001.png"
    with Image.open(BytesIO(export["content"])) as image:
        assert image.size == (600,900)
    assert any(item.label == "完成ファイルをダウンロード" for item in app.get("download_button"))
    element(app, "selectbox", "出力形式").set_value("pdf").run()
    button(app, "出力ファイルを生成")
    assert app.session_state[export_key]["content"].startswith(b"%PDF")
    element(app, "selectbox", "出力するページ").set_value(2).run()
    assert not any(item.label == "完成ファイルをダウンロード" for item in app.get("download_button"))
    fresh = AppTest.from_file(str(APP)).run()
    button(fresh, "選択したプロジェクトを開く")
    assert element(fresh, "selectbox", "レイアウトテンプレート").value == "grid"
    assert element(fresh, "text_area", "吹き出しの本文").value == "月へ！"
    assert element(fresh, "selectbox", "文字の方向").value == "horizontal"


def test_custom_frames_validation_and_bubble_addition(workspace):
    app, store = workspace
    element(app, "checkbox", "コマの配置を手動調整").check().run()
    element(app, "number_input", "1コマ：左から（%）").set_value(99.0)
    button(app, "このページのコマ割りを保存")
    assert any("枠の外" in error.value for error in app.error)
    assert not store.path.exists()
    element(app, "number_input", "1コマ：左から（%）").set_value(0.0)
    button(app, "このページのコマ割りを保存")
    assert store.load()["pages"]["1"]["frames"]
    element(app, "selectbox", "追加する文章の種類").set_value("custom")
    button(app, "吹き出しを追加")
    assert len(store.load()["pages"]["1"]["bubbles"]) == 7
    last = store.load()["pages"]["1"]["bubbles"][-1]
    element(app, "selectbox", "編集する吹き出し").set_value(last["id"]).run()
    assert element(app, "checkbox", "この吹き出し専用の文章を使う").disabled
    element(app, "text_area", "吹き出しの本文").set_value("効果音")
    button(app, "吹き出しを保存")
    assert store.load()["pages"]["1"]["bubbles"][-1]["text"] == "効果音"


def test_invalid_font_and_overflow_are_actionable_errors(workspace):
    app, store = workspace
    element(app, "text_input", "日本語フォントのファイルパス（空欄で自動検出）").set_value("/missing/font.ttf")
    button(app, "ページ共通設定を保存")
    button(app, "ページをプレビュー・再描画")
    assert any("フォント" in error.value for error in app.error)
    button(app, "出力ファイルを生成")
    assert any("フォント" in error.value for error in app.error)
    assert not any(item.label == "完成ファイルをダウンロード" for item in app.get("download_button"))
    element(app, "text_input", "日本語フォントのファイルパス（空欄で自動検出）").set_value("")
    button(app, "ページ共通設定を保存")
    element(app, "checkbox", "この吹き出し専用の文章を使う").check().run()
    element(app, "text_area", "吹き出しの本文").set_value("物語" * 2000)
    button(app, "吹き出しを保存")
    button(app, "出力ファイルを生成")
    assert any("収まりません" in error.value for error in app.error)
    assert not any(item.label == "完成ファイルをダウンロード" for item in app.get("download_button"))
