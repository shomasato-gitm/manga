from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from manga.artwork_storage import ArtworkStore
from manga.storage import ProjectStore
from manga.story_storage import StoryStore


APP = Path(__file__).resolve().parents[1] / "app.py"


class Upload(BytesIO):
    def __init__(self, content, name="ChatGPT.png"):
        super().__init__(content)
        self.name = name


def png(color="red"):
    output = BytesIO()
    Image.new("RGB", (24, 32), color).save(output, format="PNG")
    return output.getvalue()


from ui_helpers import element, button, go


@pytest.fixture
def workspace(tmp_path, monkeypatch, manga_project, story):
    import socket
    import streamlit as st

    def no_network(*args, **kwargs):
        raise AssertionError("外部通信を行ってはいけません。")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    version = StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    uploads = {}
    # AppTestはファイル選択を提供しないため、この入力境界だけファイルオブジェクトを渡す。
    # 画像検証・PNG化・ファイル保存・JSON更新は本番と同じ処理を通す。
    monkeypatch.setattr(st, "file_uploader", lambda label, **kwargs: uploads.get(label))
    app = AppTest.from_file(str(APP)).run()
    button(app, "選択したプロジェクトを開く")
    return app, ArtworkStore(tmp_path, manga_project.id), version, uploads


def test_character_reference_direction_and_candidate_workflow(workspace):
    app, store, _, uploads = workspace
    element(app, "text_area", "プロジェクトの作風").set_value("淡い色彩の水彩漫画")
    button(app, "作風を保存")
    element(app, "text_input", "名前").set_value("ルナ")
    element(app, "text_input", "髪色").set_value("銀色")
    element(app, "text_input", "年齢").set_value("17歳")
    button(app, "キャラクターを保存")
    character = store.load()["characters"][0]
    assert element(app, "selectbox", "編集するキャラクター").value == character["id"]
    element(app, "selectbox", "生成する向き・種類").set_value("全身").run()
    element(app, "checkbox", "詳細を表示（プロンプト全文）").check().run()
    assert any("向き・種類：全身" in item.value and "銀色" in item.value for item in app.code)

    uploads["キャラクターの参照画像"] = Upload(png())
    button(app, "参照画像を保存")
    uploads["キャラクターの参照画像"] = Upload(png("blue"), "斜め.png")
    element(app, "selectbox", "参照画像の種類").set_value("斜め").run()
    button(app, "参照画像を保存")
    references = store.load()["characters"][0]["references"]
    element(app, "selectbox", "基準にする参照画像").set_value(references[1]["id"]).run()
    button(app, "基準画像を選択")
    assert store.load()["characters"][0]["primary_reference_id"] == references[1]["id"]

    element(app, "multiselect", "登場キャラクター").set_value([character["id"]])
    element(app, "text_area", "場所").set_value("時計塔")
    element(app, "text_area", "表情").set_value("驚き")
    element(app, "text_area", "カメラ角度").set_value("ローアングル")
    button(app, "このコマの作画指示を保存")
    go(app,"画像制作・画像管理","画像プロンプト一括出力")
    element(app, "checkbox", "詳細を表示（プロンプト全文）").check().run()
    assert any("時計塔" in item.value and "ローアングル" in item.value and "吹き出し" in item.value for item in app.code)

    uploads["このコマの候補画像"] = Upload(png())
    button(app, "候補画像を保存")
    button(app, "選んだ候補を採用")
    uploads["このコマの候補画像"] = Upload(png("green"), "候補2.png")
    button(app, "候補画像を保存")
    entry = store.load()["panels"]["1:1"]
    element(app, "selectbox", "採用候補を選ぶ").set_value(entry["candidates"][1]["id"]).run()
    button(app, "選んだ候補を採用")
    entry = store.load()["panels"]["1:1"]
    assert entry["selected_image_id"] == entry["candidates"][1]["id"]
    assert len(entry["selection_history"]) == 2
    assert len(list(store.root.rglob("*.png"))) == 4

    fresh = AppTest.from_file(str(APP)).run()
    button(fresh, "選択したプロジェクトを開く")
    element(fresh, "selectbox", "編集するキャラクター").set_value(character["id"]).run()
    assert not fresh.exception
    assert element(fresh, "text_input", "髪色").value == "銀色"
    assert element(fresh, "text_area", "場所").value == "時計塔"
    assert element(fresh, "selectbox", "採用候補を選ぶ").value == entry["selected_image_id"]


def test_character_edit_and_delete_keep_reference_files(workspace):
    app, store, _, uploads = workspace
    element(app, "text_input", "名前").set_value("ルナ")
    button(app, "キャラクターを保存")
    uploads["キャラクターの参照画像"] = Upload(png())
    button(app, "参照画像を保存")
    element(app, "text_input", "名前").set_value("ルナ改")
    button(app, "キャラクターを保存")
    assert len(store.load()["characters"]) == 1
    element(app, "checkbox", "「ルナ改」をキャラクター一覧から削除する").check().run()
    button(app, "このキャラクターを削除")
    assert store.load()["characters"][0]["deleted_at"]
    assert element(app, "selectbox", "編集するキャラクター").options == ["新規キャラクター"]
    assert len(list(store.root.rglob("*.png"))) == 1


def test_invalid_and_missing_uploads_show_error(workspace):
    app, store, _, uploads = workspace
    button(app, "候補画像を保存")
    assert any("ファイルを選択" in item.value for item in app.error)
    uploads["このコマの候補画像"] = Upload(b"bad image")
    button(app, "候補画像を保存")
    assert any("画像を読み込めません" in item.value for item in app.error)
    assert store.load()["panels"] == {}


def test_story_changes_warn_without_discarding_candidates(workspace, story):
    app, store, version, uploads = workspace
    button(app, "このコマの作画指示を保存")
    uploads["このコマの候補画像"] = Upload(png())
    button(app, "候補画像を保存")
    button(app, "選んだ候補を採用")
    story["pages"][0]["panels"][0]["description"] = "変更後の場面。"
    store.story_store.save(story, expected_version_id=version["id"])
    app.run()
    assert not app.exception
    go(app,"作画指示・AI評価改善","コマごとの編集")
    assert any("以前の作画指示" in item.value for item in app.warning)
    go(app,"画像制作・画像管理","コマ画像・採用一覧")
    assert any("以前のストーリー" in item.value for item in app.warning)
    assert len(store.load()["panels"]["1:1"]["candidates"]) == 1


def test_removed_panels_have_accessible_image_history(workspace, story):
    app, store, version, uploads = workspace
    element(app, "selectbox", "画像を管理するコマ").set_value(2).run()
    uploads["このコマの候補画像"] = Upload(png())
    button(app, "候補画像を保存")
    story["pages"][0]["panels"].pop()
    store.story_store.save(story, expected_version_id=version["id"])
    app.run()
    assert not app.exception
    assert any("現在のストーリーにないコマ" in item.label for item in app.expander)
    assert "1:3" in store.load()["panels"]
    assert element(app, "selectbox", "過去のコマ").value == "1:3"
