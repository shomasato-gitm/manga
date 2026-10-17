import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from manga.storage import ProjectStore
from manga.story_storage import StoryStore


APP = Path(__file__).resolve().parents[1] / "app.py"


from ui_helpers import element, button, go


def textarea(app,label):
    return element(app,"text_area",label)


def start_app(tmp_path, monkeypatch, manga_project):
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    app = AppTest.from_file(str(APP)).run()
    button(app, "選択したプロジェクトを開く")
    go(app,"ストーリー制作","生成・取り込み")
    return app


def test_offline_import_edit_restore_and_restart(tmp_path, monkeypatch, manga_project, story):
    # 外部通信できない状態でも、プロンプト生成から履歴復元まで使える。
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("アプリから外部通信を行ってはいけません。")

    monkeypatch.setattr(socket, "create_connection", no_network)
    app = start_app(tmp_path, monkeypatch, manga_project)
    element(app,"checkbox","詳細を表示（プロンプト全文）").check().run()
    assert manga_project.synopsis in app.code[0].value
    textarea(app, "ChatGPTのJSONを貼り付け").set_value("```json\n" + json.dumps(story, ensure_ascii=False) + "\n```")
    button(app, "検証してストーリーを保存")
    assert app.success
    store = StoryStore(tmp_path, manga_project.id)
    original = store.load_history()[0]
    go(app,"ストーリー制作","ページ編集")
    assert store.load_history()[-1]["story"]==story

    element(app,"selectbox","編集するページ").set_value(1).run()
    textarea(app, "1コマ目の描写").set_value("司書が空に浮かぶ。")
    textarea(app, "このページの概要").set_value("司書が月の外へ出る。")
    button(app, "このページを保存")
    history = store.load_history()
    assert len(history) == 2
    assert history[-1]["story"]["pages"][1]["panels"][0]["description"] == "司書が空に浮かぶ。"
    assert history[-1]["story"]["pages"][0] == story["pages"][0]

    element(app,"selectbox","復元するバージョン").set_value(original["id"]).run()
    button(app, "このバージョンを復元")
    assert len(store.load_history()) == 3
    assert store.load_history()[-1]["story"] == story

    fresh = start_app(tmp_path, monkeypatch, manga_project)
    assert not fresh.exception
    assert textarea(fresh, "1コマ目の描写").value == story["pages"][0]["panels"][0]["description"]


def test_bad_import_and_edit_do_not_change_history(tmp_path, monkeypatch, manga_project, story):
    store = StoryStore(tmp_path, manga_project.id)
    store.save(story, expected_version_id=None)
    app = start_app(tmp_path, monkeypatch, manga_project)
    textarea(app, "ChatGPTのJSONを貼り付け").set_value("{broken")
    button(app, "検証してストーリーを保存")
    assert any("行" in item.value for item in app.error)
    assert len(store.load_history()) == 1
    textarea(app, "1コマ目の描写").set_value(" ")
    button(app, "このページを保存")
    assert any("描写" in item.value for item in app.error)
    assert len(store.load_history()) == 1


def test_upload_requires_file(tmp_path, monkeypatch, manga_project):
    app = start_app(tmp_path, monkeypatch, manga_project)
    element(app,"radio","取り込み方法").set_value("JSONファイル").run()
    assert len(app.get("file_uploader")) == 1
    button(app, "検証してストーリーを保存")
    assert "ファイルを選択" in app.error[0].value


def test_project_switch_does_not_mix_stories(tmp_path, monkeypatch, manga_project, story):
    from dataclasses import replace
    from uuid import uuid4

    other = replace(manga_project, id=str(uuid4()), title="別の漫画")
    ProjectStore(tmp_path).save(other)
    StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    app = start_app(tmp_path, monkeypatch, manga_project)
    next(item for item in app.selectbox if item.label == "プロジェクトを選択").set_value(manga_project.id).run()
    button(app, "選択したプロジェクトを開く")
    go(app,"ストーリー制作","ページ編集")
    assert element(app,"text_area","1コマ目の描写").value==story["pages"][0]["panels"][0]["description"]
    next(item for item in app.selectbox if item.label == "プロジェクトを選択").set_value(other.id).run()
    button(app, "選択したプロジェクトを開く")
    go(app,"ストーリー制作","ページ編集")
    assert not any(item.label == "編集するページ" for item in app.selectbox)


def test_project_edit_preserves_story_and_warns_page_mismatch(tmp_path, monkeypatch, manga_project, story):
    store = StoryStore(tmp_path, manga_project.id)
    original = store.save(story, expected_version_id=None)
    app = start_app(tmp_path, monkeypatch, manga_project)
    go(app,"プロジェクト管理")
    app.number_input(key="page_count").set_value(12)
    button(app, "プロジェクトを保存")
    assert ProjectStore(tmp_path).load(manga_project.id).page_count == 12
    assert store.load_history() == [original]
    go(app,"ストーリー制作","生成・取り込み")
    assert any("8ページ" in item.value and "12ページ" in item.value for item in app.warning)
    textarea(app, "ChatGPTのJSONを貼り付け").set_value(json.dumps(story))
    button(app, "検証してストーリーを保存")
    assert any("12ページ必要" in item.value for item in app.error)
    assert store.load_history() == [original]
