import copy
import json
from io import BytesIO
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from manga.artwork_storage import ArtworkStore
from manga.editorial_models import MODES, SUBJECTIVE_NOTICE
from manga.editorial_prompts import analysis_template, evaluation_template
from manga.editorial_storage import EditorialStore
from manga.editorial_workflow import workflow_status
from manga.storage import ProjectStore
from manga.story_storage import StoryStore


APP = Path(__file__).resolve().parents[1] / "app.py"


from ui_helpers import element, button, go


@pytest.fixture
def workspace(tmp_path, monkeypatch, manga_project, story):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("外部通信をしてはいけません。")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    version = StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    artwork = ArtworkStore(tmp_path, manga_project.id)
    artwork.save_character({"name": "ルナ", "hair_color": "銀色", "clothing": "青いローブ"}, expected_revision=0)
    app = AppTest.from_file(str(APP), default_timeout=10).run()
    button(app, "選択したプロジェクトを開く")
    return app, EditorialStore(tmp_path, manga_project.id), version, artwork


def evaluate_via_ui(app, version, points=5):
    payload = evaluation_template(version["id"])
    for item in payload["criteria"].values():
        item["score"] = points
    element(app, "text_area", "ストーリー評価JSONを貼り付け").set_value(json.dumps(payload, ensure_ascii=False))
    button(app, "評価JSONを検証して保存")


def test_reference_analysis_evaluation_improvement_reevaluation_and_adoption(workspace, story):
    app, store, original, artwork = workspace
    art_before = artwork.path.read_bytes()
    go(app,"ストーリー制作","ストーリー評価・改善")
    assert any(SUBJECTIVE_NOTICE in item.value for item in app.info)
    element(app, "text_input", "作品名").set_value("架空の参考作品")
    element(app, "text_input", "媒体").set_value("小説")
    element(app, "text_area", "参考にしたいストーリー技法").set_value("導入で小さな謎を置く")
    button(app, "参考作品を保存")
    reference = store.load()["references"][0]
    payload = analysis_template(reference["id"])
    element(app, "text_area", "参考作品の分析JSONを貼り付け").set_value(json.dumps(payload))
    button(app, "分析JSONを検証して保存")
    element(app, "multiselect", "ストーリー生成に使う参考作品（任意・3作品まで）").set_value([reference["id"]]).run()
    element(app,"checkbox","詳細を表示（プロンプト全文）").check().run()
    assert any("導入で小さな謎を置く" in item.value and "オリジナル" in item.value for item in app.code)
    evaluate_via_ui(app, original)
    element(app, "radio", "改善方式").set_value(MODES[1]).run()
    element(app, "multiselect", "改善したい評価項目").set_value(["dialogue"]).run()
    element(app, "multiselect", "改善するページ・コマ").set_value(["1ページ・1コマ"]).run()
    element(app, "text_area", "今回の改善理由・メモ").set_value("セリフの情報量を減らす")
    improved = copy.deepcopy(story)
    improved["pages"][0]["panels"][0]["dialogue"] = "ルナ：この本を、届けよう。"
    element(app, "text_area", "改善版ストーリーJSONを貼り付け").set_value(json.dumps(improved, ensure_ascii=False))
    button(app, "改善版を検証して新しい版に保存")
    current = store.stories.load_history()[-1]
    assert current["story"] == improved
    assert current["revision_context"]["reason"] == "セリフの情報量を減らす"
    assert element(app, "selectbox", "評価・改善する版").value == current["id"]
    evaluate_via_ui(app, current, 8)
    go(app,"ストーリー制作","比較・脚本確定")
    differences = next(item.value for item in app.table if "差（比較先−比較元）" in item.value.columns)
    assert differences["差（比較先−比較元）"].tolist() == [3] * 8
    actual = next(item.value for item in app.table if "変更前" in item.value.columns)
    assert actual["項目"].tolist() == ["セリフ"]
    assert actual.iloc[0]["変更前"] == story["pages"][0]["panels"][0]["dialogue"]
    assert actual.iloc[0]["変更後"] == improved["pages"][0]["panels"][0]["dialogue"]
    button(app, "現在の脚本を確定")
    assert workflow_status(store.load(), store.stories.load_history())[2]
    element(app, "text_area", "1コマ目の描写").set_value("確定後に描写を修正した。")
    button(app, "このページを保存")
    go(app,"ストーリー制作","比較・脚本確定")
    assert any("脚本確定後" in item.value for item in app.warning)
    element(app, "selectbox", "採用する脚本の版").set_value(original["id"]).run()
    button(app, "この版を脚本として採用")
    assert store.stories.load_history()[-1]["story"] == story
    assert len(store.load()["evaluations"]) == 2
    assert artwork.path.read_bytes() == art_before
    fresh = AppTest.from_file(str(APP)).run()
    button(fresh, "選択したプロジェクトを開く")
    assert not fresh.exception
    go(fresh,"ストーリー制作","比較・脚本確定")
    versions = next(item.value for item in fresh.table if "評価の対応" in item.value.columns)
    assert "同一JSONの採用元版の評価" in versions["評価の対応"].tolist()


def test_partial_import_failure_does_not_change_current_story(workspace, story):
    app, store, original, _ = workspace
    evaluate_via_ui(app, original)
    element(app, "radio", "改善方式").set_value(MODES[1]).run()
    element(app, "multiselect", "改善するページ・コマ").set_value(["1ページ・1コマ"]).run()
    wrong = copy.deepcopy(story)
    wrong["pages"][1]["panels"][0]["dialogue"] = "対象外の変更"
    element(app, "text_area", "改善版ストーリーJSONを貼り付け").set_value(json.dumps(wrong))
    button(app, "改善版を検証して新しい版に保存")
    assert any("指定範囲外" in item.value for item in app.error)
    assert len(store.stories.load_history()) == 1


def test_selected_reference_can_be_deleted_without_breaking_generation(workspace):
    app, store, _, _ = workspace
    element(app, "text_input", "作品名").set_value("作品")
    element(app, "text_input", "媒体").set_value("漫画")
    button(app, "参考作品を保存")
    reference = store.load()["references"][0]
    element(app, "multiselect", "ストーリー生成に使う参考作品（任意・3作品まで）").set_value([reference["id"]]).run()
    element(app, "checkbox", "この参考作品を一覧から削除する").check().run()
    button(app, "参考作品を削除")
    assert store.load()["references"][0]["deleted_at"]
    go(app,"ストーリー制作","生成・取り込み")
    element(app,"checkbox","詳細を表示（プロンプト全文）").check().run()
    assert any(item.value.startswith("あなたは日本語の漫画脚本家") for item in app.code)


def test_evaluation_file_import_and_wrong_json_are_separate(workspace, monkeypatch, story):
    import streamlit as st

    app, store, version, _ = workspace
    element(app, "text_area", "ストーリー評価JSONを貼り付け").set_value(json.dumps(story))
    button(app, "評価JSONを検証して保存")
    assert any("story_version_id" in item.value for item in app.error)
    assert store.load()["evaluations"] == []
    payload = evaluation_template(version["id"])
    uploaded = BytesIO(json.dumps(payload).encode("utf-8-sig"))
    # ファイル選択部分のみ模擬し、UTF-8読込・形式検証・履歴保存は実処理を通す。
    monkeypatch.setattr(st, "file_uploader", lambda label, **kwargs: uploaded if label == "ストーリー評価JSONファイル" else None)
    element(app, "radio", "ストーリー評価JSONの入力方法").set_value("JSONファイル").run()
    button(app, "評価JSONを検証して保存")
    assert len(store.load()["evaluations"]) == 1
