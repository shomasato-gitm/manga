import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from manga.artwork_storage import ArtworkStore
from manga.assistant_storage import AssistantStore
from manga.page_storage import PageStore
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore
from manga.studio_assets import asset_token
from manga.studio_storage import StudioStore
from manga.studio_workflow import progress, panel_states
from manga.storage_usage import project_usage
from manga.ui_data import read_store, clear_cache, cache_stats


@pytest.fixture
def workspace(tmp_path,manga_project,story):
    ProjectStore(tmp_path).save(manga_project)
    StoryStore(tmp_path,manga_project.id).save(story,expected_version_id=None)
    return tmp_path,manga_project


def snapshot(workspace):
    root,project=workspace
    art=ArtworkStore(root,project.id).load()
    history=StoryStore(root,project.id).load_history()
    assistant=AssistantStore(root,project.id).load()
    composition=PageStore(root,project.id).load()
    studio=StudioStore(root,project.id).load()
    token=asset_token(root/project.id,art,composition["settings"])
    return progress(project,history,art,assistant,composition,studio,token,root/project.id)


def test_progress_uses_saved_artifacts_not_request_clicks(workspace):
    root,project=workspace
    before,next_step,_=snapshot(workspace)
    assert before[0]["state"]==before[1]["state"]=="完了"
    assert next_step["menu"]=="キャラクター設定"
    assistant=AssistantStore(root,project.id)
    assistant.create_request(project,"characters",expected_revision=0)
    rows,_,_=snapshot(workspace)
    assert rows[2]["state"]!="完了" and rows[4]["state"]!="完了"
    assert rows[-1]["state"]=="未着手"


def test_layout_and_export_records_invalidate_after_source_change(workspace):
    root,project=workspace
    art=ArtworkStore(root,project.id);latest=art.story_store.load_history()[-1]
    art.save_direction(1,1,{"location":"時計塔"},story_version_id=latest["id"],expected_revision=0)
    data=art.load();composition=PageStore(root,project.id).load()
    fingerprint=[latest["id"],data["revision"],0,asset_token(root/project.id,data,composition["settings"])]
    studio=StudioStore(root,project.id)
    for number in range(1,9):studio.record_render(number,fingerprint)
    studio.record_export(dict(fingerprint=fingerprint,pages=list(range(1,9)),kind="pdf",filename="manga.pdf"))
    rows,_,_=snapshot(workspace)
    assert rows[-2]["state"]==rows[-1]["state"]=="完了"
    changed=copy.deepcopy(latest["story"]);changed["pages"][0]["panels"][0]["description"]="修正後"
    art.story_store.save(changed,expected_version_id=latest["id"])
    rows,_,panels=snapshot(workspace)
    assert rows[4]["state"]==rows[-2]["state"]==rows[-1]["state"]=="要確認"
    assert panels[0]["stale"]


def test_optional_evaluation_does_not_block_required_next_step(workspace):
    root,project=workspace
    rows,next_step,_=snapshot(workspace)
    assert rows[5]["optional"] and next_step["label"]!="作画評価・改善（任意）"
    store=StudioStore(root,project.id)
    before=(root/f"{project.id}.json").read_bytes()
    store.update(lambda d:d.update(skip_evaluation=True))
    assert store.load()["skip_evaluation"] and (root/f"{project.id}.json").read_bytes()==before


def test_panel_inventory_states_and_missing_assets(workspace):
    root,project=workspace;art=ArtworkStore(root,project.id);latest=art.story_store.load_history()[-1]
    assert panel_states(latest["story"],art.load())[0]["status"]=="作画指示未設定"
    art.save_direction(1,1,{},story_version_id=latest["id"],expected_revision=0)
    assert panel_states(latest["story"],art.load())[0]["status"]=="画像未登録"
    from io import BytesIO
    from PIL import Image
    out=BytesIO();Image.new("RGB",(20,20),"red").save(out,"PNG")
    image=art.add_panel_image(1,1,out.getvalue(),"test.png",story_version_id=latest["id"],expected_revision=1)
    assert panel_states(latest["story"],art.load())[0]["status"]=="候補あり"
    art.select_panel_image(1,1,image["id"],expected_revision=2)
    data=art.load()
    assert panel_states(latest["story"],data,art.root)[0]["status"]=="採用済み"
    # ファイルを消さず、メタデータだけ欠損を模擬する。
    data["panels"]["1:1"]["candidates"][0]["path"]="images/panels/missing.png"
    assert panel_states(latest["story"],data,art.root)[0]["stale"]
    assert art.image_bytes(image)


def test_display_cache_returns_copies_and_invalidates_on_save(workspace):
    root,project=workspace;store=ArtworkStore(root,project.id);clear_cache()
    assert read_store(store)["revision"]==0
    result=read_store(store);result["style"]="キャッシュを壊す変更"
    assert read_store(store)["style"]!="キャッシュを壊す変更"
    store.save_style("フルカラー",expected_revision=0)
    assert read_store(store)["style"]=="フルカラー"
    assert cache_stats()["hits"]>=2


def test_cache_detects_same_size_external_edit_and_corruption(workspace):
    root,project=workspace;store=ArtworkStore(root,project.id)
    store.save_style("abc",expected_revision=0);clear_cache();read_store(store)
    original=store.path.stat();store.path.write_text(store.path.read_text().replace('"abc"','"xyz"'))
    os.utime(store.path,ns=(original.st_atime_ns,original.st_mtime_ns))
    assert read_store(store)["style"]=="xyz"
    store.path.write_text('{broken')
    with pytest.raises(StorageError):read_store(store)
    assert store.path.read_text()=='{broken'


def test_capacity_matches_bytes_and_never_traverses_symlinks(workspace,tmp_path):
    root,project=workspace;folder=root/project.id
    (folder/"images").mkdir(exist_ok=True)
    picture=folder/"images"/"sample.png";picture.write_bytes(b"x"*37)
    outside=tmp_path/"outside";outside.mkdir();(outside/"private.json").write_text('x'*1000)
    (folder/"external").symlink_to(outside,target_is_directory=True)
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    usage=project_usage(root,project.id)
    expected=(root/f"{project.id}.json").stat().st_size+(folder/"story_history.json").stat().st_size
    assert usage["json_bytes"]==expected and usage["image_bytes"]==37
    assert usage["total_bytes"]==expected+37 and usage["warnings"]
    assert all(p.read_bytes()==content for p,content in before.items())


def test_failed_ui_record_save_keeps_previous_metadata(workspace):
    root,project=workspace;store=StudioStore(root,project.id)
    store.update(lambda d:d.update(skip_evaluation=True));before=store.path.read_bytes()
    with patch("manga.studio_storage.write_json_atomic",side_effect=StorageError("disk full")):
        with pytest.raises(StorageError):store.update(lambda d:d.update(skip_evaluation=False))
    assert store.path.read_bytes()==before


def test_export_progress_reports_real_pages(workspace):
    from manga.page_exports import export_book
    from manga.japanese_text import find_japanese_font
    root,project=workspace;art=ArtworkStore(root,project.id);latest=art.story_store.load_history()[-1]
    data=PageStore(root,project.id).load();data["settings"]["width"]=600
    calls=[]
    content,_=export_book(latest["story"],latest["id"],art.load(),data,art,find_japanese_font(),progress=lambda done,total:calls.append((done,total)))
    assert content.startswith(b"%PDF") and calls==[(i,8) for i in range(1,9)]


@pytest.mark.parametrize("content", ['{bad','{"schema_version": true}', '{"schema_version":1,"project_id":"wrong"}'])
def test_corrupt_ui_metadata_is_not_overwritten(workspace,content):
    root,project=workspace;store=StudioStore(root,project.id)
    store.path.write_text(content)
    with pytest.raises(StorageError):store.update(lambda d:d.update(skip_evaluation=True))
    assert store.path.read_text()==content
