import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from manga.assistant_samples import sample_response
from manga.assistant_storage import AssistantStore
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore


APP = Path(__file__).resolve().parents[1] / "app.py"


def element(app, kind, label):
    return next(item for item in getattr(app,kind) if item.label == label)


def button(app,label):
    element(app,"button",label).click().run(timeout=20)
    assert not app.exception


@pytest.fixture
def workspace(tmp_path,monkeypatch,manga_project,story):
    import socket
    def no_network(*args,**kwargs):
        raise AssertionError("AI制作アシスタントから外部通信してはいけません。")
    monkeypatch.setattr(socket,"create_connection",no_network)
    monkeypatch.setenv("MANGA_DATA_DIR",str(tmp_path))
    ProjectStore(tmp_path).save(manga_project)
    StoryStore(tmp_path,manga_project.id).save(story,expected_version_id=None)
    app=AppTest.from_file(str(APP)).run()
    button(app,"選択したプロジェクトを開く")
    return app,AssistantStore(tmp_path,manga_project.id)


def import_response(app,store,kind,label,mutate=None):
    req=[item for item in store.load()["requests"] if item["kind"]==kind][-1]
    response=sample_response(req)
    if mutate:mutate(response)
    element(app,"text_area",label+"を貼り付け").set_value("```json\n"+json.dumps(response,ensure_ascii=False)+"\n```")
    button(app,label+"を検証して取り込む")
    return store.load()["proposals"][-1]


def approve(app,key_fragment,one_label=None):
    candidates=[item for item in app.checkbox if key_fragment in (item.key or "") and item.label.startswith("採用候補：")]
    if one_label:
        next(item for item in candidates if item.label==one_label).check().run()
    else:
        for item in candidates:item.check()
        app.run()
    next(item for item in app.checkbox if key_fragment in (item.key or "") and item.label=="確認事項と差分を確認し、選択した提案の保存を承認する").check().run()
    next(item for item in app.button if key_fragment in (item.key or "") and item.label=="選択した提案を採用").click().run(timeout=20)
    assert not app.exception


def test_character_prompt_import_edit_approve_and_reopen(workspace):
    app,store=workspace
    assert not store.path.exists() and not store.artwork.path.exists()
    button(app,"キャラクター一括生成の依頼を作成")
    assert any("事実" in block.value and "request_id" in block.value for block in app.code)
    assert any(item.label=="動作確認用の返答サンプルJSON" for item in app.get("download_button"))
    p=import_response(app,store,"characters","キャラクター提案JSON")
    assert not store.artwork.path.exists()
    element(app,"text_area","提案：名前").set_value("ルナ")
    button(app,"この提案の編集を保存")
    assert store.load()["proposals"][0]["payload"]["items"][0]["provenance"]["name"]["status"]=="ユーザー編集"
    assert element(app,"button","選択した提案を採用").disabled
    approve(app,"_characters_")
    assert store.artwork.load()["characters"][0]["name"]=="ルナ"
    assert store.load()["proposals"][0]["decisions"][next(iter(p["decisions"]))]=="採用済み"
    fresh=AppTest.from_file(str(APP)).run()
    button(fresh,"選択したプロジェクトを開く")
    assert any("採用済み" in str(item.value) for item in fresh.info)
    assert len(store.artwork.load()["characters"])==1


def test_directions_evaluation_improvement_and_batch_prompt_workflow(workspace):
    app,store=workspace
    button(app,"作画指示一括生成の依頼を作成")
    p=import_response(app,store,"directions","作画指示提案JSON")
    assert len(p["decisions"])==24 and not store.artwork.path.exists()
    approve(app,"_directions_")
    assert len(store.artwork.load()["panels"])==24
    assert any(item.label=="全画像プロンプトをテキストでダウンロード" for item in app.get("download_button"))
    button(app,"作画評価の依頼を作成")
    import_response(app,store,"evaluation","作画評価JSON")
    assert any("主観的" in item.value for item in app.caption)
    # 改善を1コマだけに限定する。
    scope=next(item for item in app.selectbox if (item.key or "").endswith("_improvement_scope"))
    scope.set_value("指定コマ").run()
    button(app,"作画改善の依頼を作成")
    improved=import_response(app,store,"improvement","作画改善JSON",lambda payload:payload["items"][0]["fields"].update(camera_angle="俯瞰"))
    before=store.artwork.load()["panels"]["1:2"]
    approve(app,"_improvement_")
    assert store.artwork.load()["panels"]["1:1"]["direction"]["camera_angle"]=="俯瞰"
    assert store.artwork.load()["panels"]["1:2"]==before
    # 再評価し、評価点と保存済みの実差分を表示する。
    button(app,"作画評価の依頼を作成")
    import_response(app,store,"evaluation","作画評価JSON",lambda payload:payload["scores"]["camera"].update(score=8))
    assert any(item.label=="改善前後の作画評価・指示を比較" for item in app.expander)
    assert any("俯瞰" in str(item.value) for item in app.dataframe)
    assert len(store.load()["adoptions"])==2


def test_reference_file_import_reject_restore_and_adopt(workspace,monkeypatch):
    app,store=workspace
    button(app,"参考作品候補の依頼を作成")
    req=store.load()["requests"][-1]
    payload=sample_response(req)
    class Upload(BytesIO):
        name="reference.json"
    import streamlit as st
    monkeypatch.setattr(st,"file_uploader",lambda label,**kwargs:Upload(json.dumps(payload,ensure_ascii=False).encode("utf-8-sig")) if label=="参考作品候補JSONファイル" else None)
    element(app,"radio","参考作品候補JSONの入力方法").set_value("JSONファイル").run()
    button(app,"参考作品候補JSONを検証して取り込む")
    assert not store.editorial.path.exists()
    element(app,"checkbox","採用候補：銀河鉄道の夜").check().run()
    element(app,"button","選択した提案を見送る").click().run()
    assert not app.exception
    rejected=list(store.load()["proposals"][-1]["decisions"])
    element(app,"multiselect","確認待ちに戻す見送り提案").set_value(rejected)
    button(app,"見送りを確認待ちに戻す")
    approve(app,"_references_")
    assert store.editorial.load()["references"][0]["title"]=="銀河鉄道の夜"
    assert "要確認" in store.editorial.load()["references"][0]["notes"]


def test_invalid_import_and_stale_request_show_errors_without_writes(workspace):
    app,store=workspace
    button(app,"キャラクター一括生成の依頼を作成")
    element(app,"text_area","キャラクター提案JSONを貼り付け").set_value('{bad')
    button(app,"キャラクター提案JSONを検証して取り込む")
    assert app.error and store.load()["proposals"]==[]
    old=store.latest();changed=old["story"];changed["pages"][0]["summary"]="新版"
    store.stories.save(changed,expected_version_id=old["id"])
    app.run()
    assert not app.exception
    assert element(app,"button","キャラクター提案JSONを検証して取り込む").disabled
    assert any("古いストーリー" in item.value for item in app.warning)


def test_pending_write_has_explicit_resume_screen(workspace):
    app,store=workspace
    button(app,"キャラクター一括生成の依頼を作成")
    p=import_response(app,store,"characters","キャラクター提案JSON")
    from manga.storage import write_json_atomic
    def fail(path,data):
        if path==store.path:raise StorageError("障害")
        return write_json_atomic(path,data)
    with patch("manga.assistant_repository.write_json_atomic",side_effect=fail):
        with pytest.raises(StorageError):
            store.adopt(p["id"],list(p["decisions"]),expected_revision=2,expected_artwork_revision=0,expected_editorial_revision=0)
    app.run()
    assert any("途中で停止" in item.value for item in app.warning)
    button(app,"採用保存を再開")
    assert store.pending() is None and len(store.artwork.load()["characters"])==1


def test_partial_approval_then_edit_remaining_item(workspace):
    app,store=workspace
    button(app,"作画指示一括生成の依頼を作成")
    proposal=import_response(app,store,"directions","作画指示提案JSON")
    approve(app,"_directions_",one_label="採用候補：1ページ・1コマ")
    app.run()
    assert not app.exception and set(store.artwork.load()["panels"])=={"1:1"}
    pending=element(app,"selectbox","確認・編集する提案")
    assert len(pending.options)==23 and pending.value==proposal["payload"]["items"][1]["id"]
    pending.set_value(proposal["payload"]["items"][2]["id"]).run()
    element(app,"text_area","提案：カメラ角度").set_value("俯瞰")
    button(app,"この提案の編集を保存")
    approve(app,"_directions_",one_label="採用候補：1ページ・3コマ")
    assert set(store.artwork.load()["panels"])=={"1:1","1:3"}
    assert store.artwork.load()["panels"]["1:3"]["direction"]["camera_angle"]=="俯瞰"
