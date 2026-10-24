"""8ページ24コマを使い、手動ChatGPT連携後の1ページ目を実UIで制作する。"""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from manga.artwork_models import panel_key
from manga.artwork_storage import ArtworkStore
from manga.assistant_samples import sample_response
from manga.assistant_storage import AssistantStore
from manga.editorial_storage import EditorialStore
from manga.navigation import MENUS
from manga.page_storage import PageStore
from manga.panel_workspace import neighbor, next_unfinished, draft_token, overview_rows
from manga.storage import ProjectStore, StorageError
from manga.studio_workflow import panel_states
from manga.story_storage import StoryStore
from test_artwork_app import Upload, png
from ui_helpers import button, element, go

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def workspace(tmp_path, monkeypatch, manga_project, story):
    import socket
    import streamlit
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("外部通信を実行した"))
    monkeypatch.setenv("MANGA_DATA_DIR", str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    version = StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    art = ArtworkStore(tmp_path, manga_project.id)
    art.save_style("フルカラーの日本漫画。鮮やかな水彩。", expected_revision=0)
    character = art.save_character(dict(name="ルナ", hair_color="銀色"), expected_revision=1)
    ref = art.add_reference(character["id"], png(), "正面.png", "正面", "色と衣装を維持", expected_revision=2)
    for page in story["pages"]:
        for panel in page["panels"]:
            art.save_direction(page["page"], panel["panel"], dict(character_ids=[character["id"]], location="図書館", expression="真剣", pose="本を開く"), story_version_id=version["id"], expected_revision=art.load()["revision"])
    pages = PageStore(tmp_path, manga_project.id)
    pages.save_settings(pages.load()["settings"], expected_revision=0)
    editorial = EditorialStore(tmp_path, manga_project.id)
    editorial.save_reference(dict(title="分析メモ", medium="映画"), expected_revision=0)
    uploads = {}
    monkeypatch.setattr(streamlit, "file_uploader", lambda label, **k: uploads.get(label))
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    button(app, "選択したプロジェクトを開く")
    go(app, "イラスト制作", "コマ制作ワークスペース")
    return app, art, version, uploads, ref, manga_project


def cursor(app):
    return element(app, "selectbox", "画像を確認するページ").value+1, element(app, "selectbox", "画像を管理するコマ").value+1


def test_six_menus_nine_stages_and_three_panel_end_to_end_without_switching_screen(workspace):
    app, art, version, uploads, ref, project = workspace
    go(app, "企画・ストーリー", "ホーム・進行状況")
    stages = next(f.value for f in app.dataframe if "工程" in f.value.columns)
    assert len(stages) == 9
    assert stages.iloc[0]['工程'] == '企画・脚本の正式採用'
    assert stages.iloc[-1]['工程'] == '完成品の最終品質チェック'
    go(app, "イラスト制作", "コマ制作ワークスペース")
    assert list(MENUS) == ["企画・ストーリー", "キャラクター・作風", "絵コンテ・作画指示", "イラスト制作", "漫画ページ編集", "完成・書き出し"]
    assert element(app, "radio", "制作メニュー").options == list(MENUS)
    protected = {p: p.read_bytes() for p in (art.story_store.path, art.root/"composition.json", art.root/"editorial.json", art.root/ref["path"])}
    character_before = copy.deepcopy(art.load()["characters"])
    assert not app.code  # 長文プロンプトは初期状態で構築しない。
    element(app, "checkbox", "ルナの元画像を取得").check().run()
    assert any(d.label == "ルナの参照画像をダウンロード" for d in app.get("download_button"))
    for number in (1, 2, 3):
        assert cursor(app) == (1, number)
        assert element(app, "radio", "制作メニュー").value == "イラスト制作"
        # 同じ画面で作画指示を確認・調整し、その内容のプロンプトを取得する。
        element(app, "checkbox", "作画指示を編集").check().run()
        element(app, "text_area", "場所").set_value(f"図書館の棚{number}").run()
        assert element(app, "button", "候補画像を保存").disabled
        button(app, "このコマの作画指示を保存")
        element(app, "checkbox", "詳細を表示（プロンプト全文）").check().run()
        assert any(f"図書館の棚{number}" in c.value and "フルカラー" in c.value and "吹き出し" in c.value for c in app.code)
        assert any(d.label == "依頼プロンプトをダウンロード" for d in app.get("download_button"))
        for color in ("red", "blue"):
            uploads["このコマの候補画像"] = Upload(png(color), f"p1c{number}_{color}.png")
            button(app, "候補画像を保存")
        element(app, "checkbox", "2候補を並べて比較").check().run()
        entry = art.load()["panels"][f"1:{number}"]
        element(app, "selectbox", "採用候補を選ぶ").set_value(entry["candidates"][1]["id"]).run()
        button(app, "選んだ候補を採用")
        entry = art.load()["panels"][f"1:{number}"]
        assert len(entry["candidates"]) == 2 and entry["selected_image_id"] == entry["candidates"][1]["id"]
        assert any("採用画像を保存" in s.value for s in app.success)
        assert element(app, "selectbox", "未完了コマへ移動").value != f"1:{number}"
        button(app, "次の未完成コマへ")
    assert cursor(app) == (2, 1)
    assert len(panel_states(version["story"], art.load(), art.root)) == 24
    assert sum(r["selected"] for r in panel_states(version["story"], art.load(), art.root)) == 3
    assert art.load()["characters"] == character_before
    assert all(p.read_bytes() == content for p, content in protected.items())
    assert len(list((art.root/"images/panels").glob("*.png"))) == 6
    # 再起動しても採用と参照画像、人物IDは保持。
    fresh = AppTest.from_file(str(APP)).run()
    button(fresh, "選択したプロジェクトを開く")
    go(fresh, "イラスト制作", "コマ制作ワークスペース")
    assert art.load()["panels"]["1:3"]["selected_image_id"] == entry["selected_image_id"]


def test_unsaved_direction_survives_panel_page_and_menu_switch(workspace):
    app, art, *_ = workspace
    before = art.path.read_bytes()
    element(app, "checkbox", "作画指示を編集").check().run()
    element(app, "text_area", "場所").set_value("未保存の屋上").run()
    button(app, "次のコマ →")
    assert cursor(app) == (1, 2)
    assert not element(app, "checkbox", "作画指示を編集").value
    button(app, "← 前のコマ")
    assert element(app, "text_area", "場所").value == "未保存の屋上"
    element(app, "selectbox", "画像を確認するページ").set_value(7).run()
    go(app, "企画・ストーリー", "ホーム・進行状況")
    go(app, "イラスト制作", "コマ制作ワークスペース")
    element(app, "selectbox", "画像を確認するページ").set_value(0).run()
    assert cursor(app) == (1, 1)
    assert element(app, "text_area", "場所").value == "未保存の屋上"
    assert art.path.read_bytes() == before
    button(app, "このコマの作画指示を保存")
    assert art.load()["panels"]["1:1"]["direction"]["location"] == "未保存の屋上"
    assert art.load()["panels"]["1:2"]["direction"]["location"] == "図書館"


def test_upload_to_another_panel_does_not_destroy_unsaved_direction(workspace):
    app, art, version, uploads, *_ = workspace
    element(app, "checkbox", "作画指示を編集").check().run()
    element(app, "text_area", "場所").set_value("屋上の下書き").run()
    button(app, "次のコマ →")
    uploads["このコマの候補画像"] = Upload(png())
    button(app, "候補画像を保存")
    button(app, "選んだ候補を採用")
    button(app, "← 前のコマ")
    assert element(app, "text_area", "場所").value == "屋上の下書き"
    assert art.load()["panels"]["1:1"]["direction"]["location"] == "図書館"


def test_story_change_warns_and_keeps_images_and_old_draft(workspace):
    app, art, version, uploads, *_ = workspace
    uploads["このコマの候補画像"] = Upload(png())
    button(app, "候補画像を保存"); button(app, "選んだ候補を採用")
    images = {p: p.read_bytes() for p in art.root.rglob("*.png")}
    element(app, "checkbox", "作画指示を編集").check().run()
    element(app, "text_area", "場所").set_value("旧版の未保存入力").run()
    story = copy.deepcopy(version["story"])
    story["pages"][0]["panels"][0]["description"] = "司書が外へ飛び出す。"
    art.story_store.save(story, expected_version_id=version["id"])
    app.run()
    assert not app.exception
    assert any("以前の作画指示" in w.value for w in app.warning)
    assert any("以前の未保存入力" in w.value for w in app.warning)
    assert any(d.label == "以前の未保存入力をJSONで取得" for d in app.get("download_button"))
    assert element(app, "text_area", "場所").value == "図書館"  # 古い下書きを新しい版へ適用しない。
    assert all(p.read_bytes() == content for p, content in images.items())
    frame = next(d.value for d in app.dataframe if "作画指示" in d.value.columns)
    assert frame.iloc[0]["確認"] == "要確認"
    before = art.path.read_bytes()
    candidate = art.load()["panels"]["1:1"]["candidates"][0]
    with pytest.raises(StorageError, match="ストーリーが更新"):
        art.select_panel_image(1, 1, candidate["id"], expected_revision=art.load()["revision"], story_version_id=version["id"])
    assert art.path.read_bytes() == before


def test_workspace_only_loads_current_panel_and_reuses_thumbnail(workspace):
    app, art, version, _, *_ = workspace
    for p, c in ((1, 1), (2, 3), (8, 3)):
        art.add_panel_image(p, c, png(), f"p{p}c{c}.png", story_version_id=version["id"], expected_revision=art.load()["revision"])
    from manga.ui_data import clear_cache
    clear_cache()
    original = art.image_bytes
    seen = []
    def read(store, image):
        seen.append(image["id"])
        return original(image)
    with patch.object(ArtworkStore, "image_bytes", read), patch("manga.page_ui.render_page", side_effect=AssertionError("非表示のページを合成")):
        app.run()
        permitted = {im["id"] for im in art.load()["panels"]["1:1"]["candidates"]} | {im["id"] for c in art.load()["characters"] for im in c["references"]}
        assert set(seen) <= permitted
        assert seen
        seen.clear();app.run();assert seen == []


def test_all_images_ready_offers_page_editor(workspace):
    app, art, version, *_ = workspace
    for page in version["story"]["pages"]:
        for panel in page["panels"]:
            image = art.add_panel_image(page["page"], panel["panel"], png(), "完成.png", story_version_id=version["id"], expected_revision=art.load()["revision"])
            art.select_panel_image(page["page"], panel["panel"], image["id"], expected_revision=art.load()["revision"])
    app.run()
    assert any("制作済み 24/24コマ" in p.proto.text for p in app.get("progress"))
    button(app, "漫画ページ編集へ進む")
    assert element(app, "radio", "制作メニュー").value == "漫画ページ編集"


def test_direction_adoption_has_direct_workspace_link(workspace):
    app, art, version, _, _, project = workspace
    assistant = AssistantStore(art.root.parent, project.id)
    request = assistant.create_request(project, "directions", targets=[dict(page=1, panel=2)], expected_revision=0)
    proposal = assistant.import_proposal(request["id"], json.dumps(sample_response(request), ensure_ascii=False), expected_revision=1)
    assistant.adopt(proposal["id"], list(proposal["decisions"]), expected_revision=2, expected_artwork_revision=art.load()["revision"], expected_editorial_revision=assistant.editorial.load()["revision"])
    go(app, "絵コンテ・作画指示", "AI一括生成・採用")
    button(app, "採用したコマのイラスト制作へ")
    assert cursor(app) == (1, 2)
    assert element(app, "radio", "制作メニュー").value == "イラスト制作"


def test_save_failure_retains_draft_and_existing_bytes(workspace):
    app, art, *_ = workspace
    element(app, "checkbox", "作画指示を編集").check().run()
    element(app, "text_area", "場所").set_value("保存待ちの展望台").run()
    before = art.path.read_bytes()
    with patch("manga.artwork_storage.write_json_atomic", side_effect=StorageError("容量不足")):
        button(app, "このコマの作画指示を保存")
    assert any("容量不足" in e.value for e in app.error)
    assert art.path.read_bytes() == before
    assert element(app, "text_area", "場所").value == "保存待ちの展望台"
    button(app, "このコマの作画指示を保存")
    assert art.load()["panels"]["1:1"]["direction"]["location"] == "保存待ちの展望台"


def test_panel_helpers_and_progress_are_based_on_saved_state(story, manga_project):
    from manga.artwork_models import empty_artwork
    rows = panel_states(story, empty_artwork(manga_project.id))
    assert len(rows) == 24
    assert neighbor(story, 1, 1, -1) is None
    assert neighbor(story, 1, 3, 1) == (2, 1)
    assert neighbor(story, 8, 3, 1) is None
    assert next_unfinished(rows, "8:3") == (1, 1)
    rows[0].update(direction=True, selected=True, status="採用済み")
    assert next_unfinished(rows, "8:3") == (1, 2)
    rows[0]["stale"] = True
    assert next_unfinished(rows, "8:3") == (1, 1)
    assert overview_rows(rows)[0]["確認"] == "要確認"
    assert draft_token("v1", 1, 1, {}) != draft_token("v1", 1, 2, {})
    assert draft_token("v1", 1, 1, {}) != draft_token("v2", 1, 1, {})


def test_original_download_cache_never_returns_thumbnail_and_detects_file_change(workspace, tmp_path):
    from io import BytesIO
    from PIL import Image
    from manga.gallery_ui import thumbnail
    from manga.panel_downloads import original_bytes
    app, art, version, *_ = workspace
    content = BytesIO(); Image.new("RGB", (900, 1200), "red").save(content, "PNG")
    image = art.add_panel_image(1, 1, content.getvalue(), "large.png", story_version_id=version["id"], expected_revision=art.load()["revision"])
    assert Image.open(BytesIO(thumbnail(art, image))).size == (270, 360)
    first = original_bytes(art, image)
    assert Image.open(BytesIO(first)).size == (900, 1200)
    assert original_bytes(art, image) == first
    other = BytesIO(); Image.new("RGB", (900, 1200), "blue").save(other, "PNG")
    (art.root/image["path"]).write_bytes(other.getvalue())
    assert original_bytes(art, image) != first
    # 別プロジェクトの同名ファイルを既にキャッシュしていても、外へのリンクを読まない。
    outside = tmp_path / "outside.png"; outside.write_bytes(first)
    path = art.root/image["path"]; path.unlink(); path.symlink_to(outside)
    with pytest.raises(StorageError, match="外側"):
        original_bytes(art, image)
    with pytest.raises(StorageError, match="外側"):
        thumbnail(art, image)


def test_overview_separates_direction_and_image_status(story, manga_project):
    from manga.artwork_models import empty_artwork
    art = empty_artwork(manga_project.id)
    # 作画指示のないコマにも、以前からの画像候補を表示する。
    art["panels"]["1:1"] = dict(candidates=[dict(id="candidate")], direction=None, selected_image_id=None)
    rows = overview_rows(panel_states(story, art))
    assert rows[0]["作画指示"] == "作画指示がない"
    assert rows[0]["画像"] == "画像候補がある"


def test_related_histories_are_filtered_and_can_switch_between_stages(workspace):
    app, art, version, _, _, project = workspace
    assistant = AssistantStore(art.root.parent, project.id)
    for kind in ("characters", "references", "directions"):
        request = assistant.create_request(project, kind, expected_revision=assistant.load()["revision"])
        assistant.import_proposal(request["id"], json.dumps(sample_response(request), ensure_ascii=False), expected_revision=assistant.load()["revision"])
    for menu, section, expected in (("キャラクター・作風", "制作提案の履歴", "キャラクター一括生成"),
                                     ("絵コンテ・作画指示", "制作提案の履歴", "作画指示一括生成"),
                                     ("企画・ストーリー", "参考作品の提案履歴", "参考作品候補"),
                                     ("キャラクター・作風", "制作提案の履歴", "キャラクター一括生成")):
        go(app, menu, section)
        assert not app.exception
        assert len(element(app, "selectbox", "履歴の生成依頼").options) == 1
        assert expected in element(app, "selectbox", "履歴の生成依頼").options[0]


def test_explicit_discard_clears_workspace_draft_without_changing_saved_direction(workspace):
    app, art, *_ = workspace
    before = art.path.read_bytes()
    element(app, "checkbox", "作画指示を編集").check().run()
    element(app, "text_area", "場所").set_value("破棄する下書き").run()
    go(app, "完成・書き出し", "保存先・容量・診断")
    button(app, "セッションの下書きを破棄（保存済みデータは保持）")
    go(app, "イラスト制作", "コマ制作ワークスペース")
    assert not element(app, "checkbox", "作画指示を編集").value
    assert not element(app, "button", "候補画像を保存").disabled
    element(app, "checkbox", "作画指示を編集").check().run()
    assert element(app, "text_area", "場所").value == "図書館"
    assert art.path.read_bytes() == before
