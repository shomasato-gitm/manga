import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from manga.artwork_storage import ArtworkStore
from manga.assistant_samples import sample_response
from manga.assistant_storage import AssistantStore
from manga.editorial_storage import EditorialStore
from manga.page_storage import PageStore
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from manga.studio_storage import StudioStore
from ui_helpers import go,element,button

APP=Path(__file__).resolve().parents[1]/"app.py"


@pytest.fixture
def workspace(tmp_path,monkeypatch,manga_project,story):
    monkeypatch.setenv("MANGA_DATA_DIR",str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    StoryStore(tmp_path,manga_project.id).save(story,expected_version_id=None)
    return tmp_path,manga_project


def open_app():
    app=AppTest.from_file(str(APP),default_timeout=20).run()
    button(app,"選択したプロジェクトを開く")
    return app


def test_navigation_calls_only_selected_screen_and_never_reads_hidden_images(workspace):
    with patch("manga.artwork_storage.ArtworkStore.image_bytes",side_effect=AssertionError("非表示画像を読んだ")),patch("manga.assistant_ui.render_assistant_workspace",side_effect=AssertionError("非表示AI画面を構築した")):
        app=open_app()
        assert element(app,"radio","制作メニュー").value=="企画・ストーリー"
        assert not any(x.label=="名前" for x in app.text_input)
        go(app,"プロジェクト管理")
        assert element(app,"text_input","タイトル").value==workspace[1].title
        go(app,"履歴・設定","保存先・容量・診断")
        assert not app.exception


def test_draft_survives_menu_switch_without_saving_or_crossing_projects(workspace):
    root,project=workspace
    app=open_app();go(app,"キャラクター設定","手動設定")
    element(app,"text_input","名前").set_value("入力途中のルナ").run()
    go(app,"ホーム・進行状況");go(app,"キャラクター設定","手動設定")
    assert element(app,"text_input","名前").value=="入力途中のルナ"
    assert not ArtworkStore(root,project.id).path.exists()
    button(app,"キャラクターを保存")
    assert ArtworkStore(root,project.id).load()["characters"][0]["name"]=="入力途中のルナ"
    from dataclasses import replace
    from uuid import uuid4
    other=replace(project,id=str(uuid4()),title="別の企画")
    ProjectStore(root).save(other)
    app.run();element(app,"selectbox","プロジェクトを選択").set_value(other.id).run();button(app,"選択したプロジェクトを開く")
    go(app,"キャラクター設定","手動設定")
    assert element(app,"text_input","名前").value==""


def test_story_import_defaults_file_and_paste_draft_is_preserved(workspace):
    app=open_app();go(app,"ストーリー制作","生成・取り込み")
    assert element(app,"radio","取り込み方法").value=="JSONファイル"
    assert len(app.get("file_uploader"))==1
    element(app,"radio","取り込み方法").set_value("貼り付け").run()
    element(app,"text_area","ChatGPTのJSONを貼り付け").set_value('{"下書き":"途中"}').run()
    element(app,"radio","取り込み方法").set_value("JSONファイル").run()
    element(app,"radio","取り込み方法").set_value("貼り付け").run()
    assert element(app,"text_area","ChatGPTのJSONを貼り付け").value=='{"下書き":"途中"}'
    go(app,"ホーム・進行状況");go(app,"ストーリー制作","生成・取り込み")
    assert element(app,"text_area","ChatGPTのJSONを貼り付け").value=='{"下書き":"途中"}'


@pytest.mark.parametrize("kind,menu,section,label",[
    ("characters","キャラクター設定","AI一括生成・採用","キャラクター提案JSON"),
    ("references","ストーリー制作","参考作品のAI候補","参考作品候補JSON"),
    ("directions","作画指示・AI評価改善","AI一括生成・採用","作画指示提案JSON"),
    ("evaluation","作画指示・AI評価改善","AI評価","作画評価JSON"),
    ("improvement","作画指示・AI評価改善","AI改善","作画改善JSON"),
])
def test_assistant_file_default_prompt_tools_and_request_status(workspace,kind,menu,section,label):
    root,project=workspace;store=AssistantStore(root,project.id)
    if kind in ("evaluation","improvement"):
        latest=store.latest()
        for page in latest["story"]["pages"]:
            for panel in page["panels"]:
                store.artwork.save_direction(page["page"],panel["panel"],{},story_version_id=latest["id"],expected_revision=store.artwork.load()["revision"])
    evaluation_id=None
    if kind=="improvement":
        req=store.create_request(project,"evaluation",expected_revision=0)
        p=store.import_proposal(req["id"],json.dumps(sample_response(req),ensure_ascii=False),expected_revision=1)
        evaluation_id=p["id"]
    request=store.create_request(project,kind,expected_revision=store.load()["revision"],evaluation_id=evaluation_id)
    app=open_app();go(app,menu,section)
    assert element(app,"radio",label+"の入力方法").value=="JSONファイル"
    assert any(d.label=="依頼プロンプトをダウンロード" for d in app.get("download_button"))
    assert any(request["id"] in c.value for c in app.caption)
    assert not app.code
    element(app,"checkbox","詳細を表示（プロンプト全文）").check().run()
    assert any("request_id" in c.value for c in app.code)
    app.run()
    # 再表示では依頼を勝手に追加しない。
    assert len(store.load()["requests"])==(2 if kind=="improvement" else 1)


def test_reference_and_story_evaluation_imports_default_file(workspace):
    root,project=workspace;editorial=EditorialStore(root,project.id)
    editorial.save_reference(dict(title="作品",medium="小説"),expected_revision=0)
    app=open_app();go(app,"ストーリー制作","参考作品の分析")
    assert element(app,"radio","参考作品の分析JSONの入力方法").value=="JSONファイル"
    go(app,"ストーリー制作","ストーリー評価・改善")
    assert element(app,"radio","ストーリー評価JSONの入力方法").value=="JSONファイル"


def test_storage_scan_is_on_demand_and_diagnostics_have_real_timings(workspace):
    from manga.storage_usage import project_usage
    app=open_app()
    with patch("manga.settings_ui.project_usage",wraps=project_usage) as scan:
        go(app,"履歴・設定","保存先・容量・診断");assert scan.call_count==1
        button(app,"使用容量を再計測");assert scan.call_count==2
        app.run();assert scan.call_count==2
    assert any("MANGA_DATA_DIR" in c.value for c in app.code)
    assert app.session_state["_screen_timings"][-1]["ms"]>=0


def test_next_action_and_unfinished_panel_jump(workspace):
    app=open_app();button(app,"次の工程へ進む")
    assert element(app,"radio","制作メニュー").value=="キャラクター・作風"
    go(app,"画像制作・画像管理","コマ画像・採用一覧")
    element(app,"selectbox","未完了コマへ移動").set_value("2:3").run()
    button(app,"このコマの画像管理を開く")
    assert element(app,"selectbox","画像を確認するページ").value==1
    assert element(app,"selectbox","画像を管理するコマ").value==2


def test_preview_is_not_redrawn_on_idle_or_menu_switch_and_file_change_invalidates(workspace):
    root,project=workspace;art=ArtworkStore(root,project.id);latest=art.story_store.load_history()[-1]
    out=BytesIO();Image.new("RGB",(24,30),"red").save(out,"PNG")
    image=art.add_panel_image(1,1,out.getvalue(),"red.png",story_version_id=latest["id"],expected_revision=0)
    art.select_panel_image(1,1,image["id"],expected_revision=1)
    app=open_app();go(app,"漫画ページ編集")
    from manga.page_render import render_page
    with patch("manga.page_ui.render_page",wraps=render_page) as render:
        button(app,"ページをプレビュー・再描画");assert render.call_count==1
        app.run();assert render.call_count==1
        button(app,"ページをプレビュー・再描画");assert render.call_count==2
        go(app,"ホーム・進行状況");go(app,"漫画ページ編集");assert render.call_count==2
        out=BytesIO();Image.new("RGB",(24,30),"blue").save(out,"PNG")
        (art.root/image["path"]).write_bytes(out.getvalue())
        app.run();assert render.call_count==3
    assert StudioStore(root,project.id).load()["renders"]["1"]


def test_successful_export_updates_home_and_metadata_without_modifying_legacy_data(workspace):
    root,project=workspace
    original=(root/project.id/"story_history.json").read_bytes()
    app=open_app();go(app,"PNG・PDF書き出し")
    element(app,"selectbox","ページの横幅（px）").set_value(600);button(app,"ページ共通設定を保存")
    element(app,"selectbox","出力形式").set_value("pdf").run();button(app,"出力ファイルを生成")
    assert any(d.label=="完成ファイルをダウンロード" for d in app.get("download_button"))
    assert StudioStore(root,project.id).load()["exports"][-1]["pages"]==list(range(1,9))
    button(app,"出力ファイルを生成")
    assert len(StudioStore(root,project.id).load()["exports"])==1
    assert (root/project.id/"story_history.json").read_bytes()==original
    go(app,"ホーム・進行状況")
    frame=next(f.value for f in app.dataframe if "工程" in f.value.columns)
    assert frame.iloc[-1]["状態"]=="完了"
