import copy
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from PIL import Image

from manga.artwork_models import CHARACTER_ALL_FIELDS, DIRECTION_FIELDS, direction_fields
from manga.assistant_changes import differences
from manga.assistant_models import DRAWING_CRITERIA, normalize_payload, targets_for
from manga.assistant_prompts import batch_image_prompts
from manga.assistant_samples import sample_response
from manga.assistant_storage import AssistantStore
from manga.page_layout import default_page
from manga.page_storage import PageStore
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore


@pytest.fixture
def assistant(tmp_path, manga_project, story):
    ProjectStore(tmp_path).save(manga_project)
    StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    return AssistantStore(tmp_path, manga_project.id), manga_project


def request(workspace, kind="characters", **kwargs):
    store, project = workspace
    return store.create_request(project, kind, expected_revision=store.load()["revision"], **kwargs)


def ingest(store, req, payload=None):
    return store.import_proposal(req["id"], json.dumps(payload if payload is not None else sample_response(req), ensure_ascii=False), expected_revision=store.load()["revision"])


def adopt(store, proposal, ids=None, **kwargs):
    return store.adopt(proposal["id"], ids or list(proposal["decisions"]), expected_revision=store.load()["revision"], expected_artwork_revision=store.artwork.load()["revision"], expected_editorial_revision=store.editorial.load()["revision"], **kwargs)


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (20,30), "red").save(output, "PNG")
    return output.getvalue()


def test_character_review_edit_adopt_and_roundtrip(assistant):
    store, _ = assistant
    req = request(assistant)
    proposal = ingest(store, req)
    assert store.artwork.load()["characters"] == []
    item = copy.deepcopy(proposal["payload"]["items"][0])
    item["fields"]["name"] = "ルナ"
    item["provenance"]["name"] = dict(status="ユーザー編集", evidence="利用者が確認")
    store.edit_item(proposal["id"], item["id"], item, expected_revision=2)
    changes = adopt(store, proposal)
    character = store.artwork.load()["characters"][0]
    assert character["name"] == "ルナ" and character["role"] == "主人公候補"
    assert changes[0]["result_id"] == character["id"]
    fresh = AssistantStore(store.root.parent, store.project_id).load()
    assert fresh["proposals"][0]["original_payload"]["items"][0]["fields"]["name"] == "デモ人物A"
    assert fresh["proposals"][0]["edits"][0]["after"]["fields"]["name"] == "ルナ"
    assert fresh["proposals"][0]["decisions"][item["id"]] == "採用済み"
    with pytest.raises(ValueError, match="二重登録"):
        adopt(store, proposal)
    assert len(store.artwork.load()["characters"]) == 1


def test_character_update_keeps_id_references_primary_and_other_data(assistant):
    store, _ = assistant
    c = store.artwork.save_character({"name":"司書"}, expected_revision=0)
    ref = store.artwork.add_reference(c["id"], image_bytes(), "正面.png", "正面", expected_revision=1)
    latest = store.latest()
    pages = PageStore(store.root.parent, store.project_id)
    pages.save_page(1, default_page(store.project_id, latest["story"]["pages"][0], latest["id"]), story_version_id=latest["id"], expected_revision=0)
    before = {path:path.read_bytes() for path in (store.stories.path, pages.path, store.root.parent / f"{store.project_id}.json")}
    req = request(assistant)
    payload = sample_response(req)
    payload["items"][0]["existing_id"] = c["id"]
    payload["items"][0]["fields"]["name"] = "司書"
    p = ingest(store, req, payload)
    adopt(store, p)
    current = store.artwork.load()["characters"][0]
    assert current["id"] == c["id"] and current["primary_reference_id"] == ref["id"]
    assert len(current["references"]) == 1 and store.artwork.image_bytes(ref) == image_bytes()
    assert all(path.read_bytes() == content for path, content in before.items())


def test_character_duplicates_and_multiple_partial_adoptions(assistant):
    store, _ = assistant
    store.artwork.save_character({"name":"デモ人物A"}, expected_revision=0)
    req = request(assistant)
    payload = sample_response(req)
    second = copy.deepcopy(payload["items"][0]); second["id"] = "second"; second["fields"]["name"] = "ソラ"
    payload["items"].append(second)
    p = ingest(store, req, payload)
    with pytest.raises(ValueError, match="同名"):
        adopt(store, p)
    assert len(store.artwork.load()["characters"]) == 1
    adopt(store, p, [second["id"]])
    assert len(store.artwork.load()["characters"]) == 2
    adopt(store, p, [payload["items"][0]["id"]], allow_duplicates=True)
    assert len(store.artwork.load()["characters"]) == 3


def test_24_directions_selective_adoption_preserves_images_and_unselected(assistant):
    store, _ = assistant
    latest = store.latest()
    store.artwork.save_direction(1,2,dict(location="元の場所"), story_version_id=latest["id"], expected_revision=0)
    image = store.artwork.add_panel_image(1,1,image_bytes(),"candidate.png", story_version_id=latest["id"], expected_revision=1)
    store.artwork.select_panel_image(1,1,image["id"],expected_revision=2)
    untouched = copy.deepcopy(store.artwork.load()["panels"]["1:2"])
    req = request(assistant,"directions")
    assert len(req["targets"]) == 24
    p = ingest(store,req)
    chosen = [p["payload"]["items"][0]["id"],p["payload"]["items"][2]["id"]]
    adopt(store,p,chosen)
    art = store.artwork.load()
    assert art["panels"]["1:2"] == untouched
    assert len(art["panels"]) == 3
    assert art["panels"]["1:1"]["selected_image_id"] == image["id"]
    assert len(art["panels"]["1:1"]["candidates"]) == 1
    assert store.artwork.image_bytes(image) == image_bytes()
    assert art["panels"]["1:3"]["direction"]["color_palette"]
    adopt(store,p,[p["payload"]["items"][3]["id"]])
    assert "2:1" in store.artwork.load()["panels"]


@pytest.mark.parametrize("page,panel,count", [(None,None,24),(2,None,3),(3,2,1)])
def test_scope_and_prompt_complete_targets(assistant,page,panel,count):
    store, _ = assistant
    targets = targets_for(store.latest()["story"],page,panel)
    req = request(assistant,"directions",targets=targets)
    assert len(req["targets"]) == count
    assert "フルカラー" in request(assistant,"characters",theme="フルカラーの冒険")["prompt"]
    assert "character_ids" in req["prompt"] and "連続性" in req["prompt"]
    assert len(ingest(store,req)["payload"]["items"]) == count


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p.update(story_version_id=str(uuid4())),"story_version_id"),
    (lambda p: p.update(project_id=str(uuid4())),"project_id"),
    (lambda p: p.update(request_id=str(uuid4())),"request_id"),
    (lambda p: p["items"].pop(),"欠け"),
    (lambda p: p["items"][0].update(page=99),"範囲外"),
    (lambda p: p["items"][0].update(panel=True),"番号"),
    (lambda p: p["items"][0]["fields"].update(character_ids=[str(uuid4())]),"人物ID"),
    (lambda p: p["items"][0]["fields"].pop("pose"),"pose"),
    (lambda p: p["items"][1].update(id=p["items"][0]["id"]),"重複"),
    (lambda p: p["items"][1].update(page=1,panel=1),"重複"),
])
def test_direction_invalid_json_never_changes_any_settings(assistant,mutation,match):
    store, _ = assistant
    req = request(assistant,"directions")
    before = store.path.read_bytes()
    payload = sample_response(req);mutation(payload)
    with pytest.raises(ValueError,match=match):
        ingest(store,req,payload)
    assert store.path.read_bytes() == before and not store.artwork.path.exists()


@pytest.mark.parametrize("raw", ['bad','{"items": [],"items": []}','{"x":NaN}','[]','{"schema_version":2}'])
def test_invalid_json_errors(assistant,raw):
    store,_=assistant
    req=request(assistant)
    with pytest.raises(ValueError):
        store.import_proposal(req["id"],raw,expected_revision=1)
    assert not store.artwork.path.exists()


def test_stale_story_and_changed_characters_or_direction_are_blocked(assistant):
    store,_=assistant
    req=request(assistant,"directions")
    p=ingest(store,req)
    store.artwork.save_style("フルカラー漫画",expected_revision=0)
    with pytest.raises(StorageError,match="作風"):
        adopt(store,p)
    with pytest.raises(StorageError,match="作風"):
        ingest(store,req)
    req=request(assistant,"directions")
    p=ingest(store,req)
    store.artwork.save_direction(1,1,dict(location="利用者の修正"),story_version_id=req["story_version_id"],expected_revision=1)
    with pytest.raises(StorageError,match="作画指示"):
        adopt(store,p,[p["payload"]["items"][0]["id"]])
    old=store.latest();story=copy.deepcopy(old["story"]);story["pages"][0]["summary"]="新しい内容"
    store.stories.save(story,expected_version_id=old["id"])
    with pytest.raises(StorageError,match="ストーリーが変更"):
        ingest(store,req)
    with pytest.raises(StorageError,match="ストーリーが変更"):
        adopt(store,p)


def test_references_unverified_and_user_selected_only(assistant):
    store,_=assistant
    req=request(assistant,"references",theme="孤独から友情へ")
    payload=sample_response(req)
    second=copy.deepcopy(payload["items"][0]);second["id"]="other";second["fields"]["title"]="別の作品";payload["items"].append(second)
    p=ingest(store,req,payload)
    assert store.editorial.load()["references"] == []
    adopt(store,p,[second["id"]])
    refs=store.editorial.load()["references"]
    assert len(refs)==1 and refs[0]["title"]=="別の作品"
    assert "要確認" in refs[0]["notes"] and "自作への応用" in refs[0]["notes"]
    assert not store.artwork.path.exists()
    req2=request(assistant,"references")
    payload2=sample_response(req2);payload2["items"][0]["confirmation_status"]="ユーザー確認済み"
    with pytest.raises(ValueError,match="要確認"):
        ingest(store,req2,payload2)
    p2=ingest(store,req2)
    adopt(store,p2)
    p3=ingest(store,request(assistant,"references"))
    with pytest.raises(ValueError,match="同じ作品名"):
        adopt(store,p3)


def test_evaluate_improve_reevaluate_compare_and_export_color(assistant):
    store,project=assistant
    store.artwork.save_style("フルカラーの水彩漫画",expected_revision=0)
    req=request(assistant,"directions")
    p=ingest(store,req);adopt(store,p)
    evaluation_request=request(assistant,"evaluation")
    evaluation=ingest(store,evaluation_request)
    assert len(evaluation["payload"]["scores"])==8
    req2=request(assistant,"improvement",targets=targets_for(store.latest()["story"],1,1),evaluation_id=evaluation["id"])
    payload=sample_response(req2);payload["items"][0]["fields"]["camera_angle"]="俯瞰"
    improvement=ingest(store,req2,payload)
    changes=adopt(store,improvement)
    assert any(item["field"]=="camera_angle" and item["after"]=="俯瞰" for item in changes[0]["differences"])
    second=ingest(store,request(assistant,"evaluation"))
    assert len(store.load()["proposals"])==4 and second["id"] != evaluation["id"]
    prompts,warnings,output=batch_image_prompts(project,store.latest(),store.artwork.load())
    assert len(prompts)==24 and warnings==[]
    assert all("フルカラー" in item["prompt"] and "描き込まない" in item["prompt"] and "色彩" in item["prompt"] for item in prompts)
    assert "1ページ・1コマ" in output and "8ページ・3コマ" in output
    assert "俯瞰" in prompts[0]["prompt"]
    assert differences({"camera":"目線"},{"camera":"俯瞰"})[0]["before"]=="目線"
    with pytest.raises(StorageError,match="作画指示"):
        request(assistant,"improvement",evaluation_id=evaluation["id"])


@pytest.mark.parametrize("score", [-1,11,True,"8",None])
def test_evaluation_scores_validate(assistant,score):
    store,_=assistant
    p=ingest(store,request(assistant,"directions"));adopt(store,p)
    req=request(assistant,"evaluation")
    payload=sample_response(req);payload["scores"]["alignment"]["score"]=score
    with pytest.raises(ValueError,match="0〜10"):
        ingest(store,req,payload)


def test_no_evaluation_or_incomplete_directions_are_rejected(assistant):
    with pytest.raises(ValueError,match="未設定"):
        request(assistant,"evaluation")
    store,_=assistant
    p=ingest(store,request(assistant,"directions"));adopt(store,p)
    with pytest.raises(ValueError,match="見つかりません"):
        request(assistant,"improvement",evaluation_id=str(uuid4()))


def test_reject_restore_and_independent_projects(assistant):
    store,_=assistant
    p=ingest(store,request(assistant))
    ids=list(p["decisions"])
    store.decide(p["id"],ids,"見送り",expected_revision=2)
    with pytest.raises(ValueError,match="確認待ち"):
        adopt(store,p)
    store.decide(p["id"],ids,"確認待ち",expected_revision=3)
    adopt(store,p)
    assert AssistantStore(store.root.parent,str(uuid4())).load()["requests"]==[]


def test_optimistic_conflicts_and_concurrent_adoption(assistant):
    store,_=assistant
    req=request(assistant);p=ingest(store,req)
    with pytest.raises(StorageError,match="制作提案が更新"):
        store.edit_item(p["id"],p["payload"]["items"][0]["id"],p["payload"]["items"][0],expected_revision=0)
    def attempt(_):
        try:
            return store.adopt(p["id"],list(p["decisions"]),expected_revision=2,expected_artwork_revision=0,expected_editorial_revision=0)
        except (StorageError,ValueError):
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(attempt,range(2)))
    assert sum(value is not None for value in result)==1
    assert len(store.artwork.load()["characters"])==1


@pytest.mark.parametrize("kind", ["characters", "references"])
@pytest.mark.parametrize("failure_file", ["assistant_pending.json","target","assistant.json"])
def test_write_failure_preserves_originals_and_explicit_resume(assistant,failure_file,kind):
    store,_=assistant
    p=ingest(store,request(assistant,kind))
    target_store=store.editorial if kind=="references" else store.artwork
    target_list="references" if kind=="references" else "characters"
    if failure_file=="target":failure_file=target_store.path.name
    original=store.path.read_bytes()
    from manga.storage import write_json_atomic
    def failing(path,data):
        if path.name==failure_file:
            raise StorageError("ディスク障害の再現")
        return write_json_atomic(path,data)
    with patch("manga.assistant_repository.write_json_atomic",side_effect=failing):
        with pytest.raises(StorageError):
            adopt(store,p)
    assert store.path.read_bytes()==original
    if failure_file!="assistant_pending.json":
        assert store.pending()
        with pytest.raises(StorageError,match="途中"):
            request(assistant)
        store.resume()
        assert store.pending() is None
        assert len(target_store.load()[target_list])==1
        store.resume()
        assert len(target_store.load()[target_list])==1
    else:
        assert not target_store.path.exists()


def test_resume_never_overwrites_unrelated_edits(assistant):
    store,_=assistant
    p=ingest(store,request(assistant))
    from manga.storage import write_json_atomic
    def failing(path,data):
        if path==store.path:
            raise StorageError("停止")
        return write_json_atomic(path,data)
    with patch("manga.assistant_repository.write_json_atomic",side_effect=failing):
        with pytest.raises(StorageError):adopt(store,p)
    store.artwork.save_style("他画面の編集",expected_revision=1)
    before=store.artwork.path.read_bytes()
    with pytest.raises(StorageError,match="自動上書きは停止"):
        store.resume()
    assert store.artwork.path.read_bytes()==before


def test_corrupt_file_and_journal_are_not_overwritten(assistant):
    store,_=assistant
    store.path.parent.mkdir(exist_ok=True)
    store.path.write_text('{"schema_version":999}')
    with pytest.raises(StorageError,match="上書きせず"):
        request(assistant)
    assert store.path.read_text()=='{"schema_version":999}'
    store.journal_path.write_text('{bad')
    with pytest.raises(StorageError,match="不正"):
        store.resume()
    assert store.journal_path.read_text()=='{bad'


@pytest.mark.parametrize("kind", ["characters","directions","references","evaluation","improvement"])
def test_shipped_sample_json_uses_valid_schema(kind):
    from pathlib import Path
    from manga.artwork_models import empty_artwork
    root=Path(__file__).resolve().parents[1]
    payload=json.loads((root/"examples"/"assistant"/f"{kind}.json").read_text())
    story=json.loads((root/"examples"/"story_8pages.json").read_text())
    req=dict(id=payload["request_id"],project_id=payload["project_id"],story_version_id=payload["story_version_id"],kind=kind,
             targets=targets_for(story) if kind in ("directions","evaluation","improvement") else [],
             context=dict(story=story,artwork=empty_artwork(payload["project_id"]),references=[]))
    assert normalize_payload(payload,req)==payload


def test_character_provenance_and_unknown_existing_ids_validate(assistant):
    store,_=assistant
    req=request(assistant)
    payload=sample_response(req)
    payload["items"][0]["provenance"].pop("name")
    with pytest.raises(ValueError,match="根拠"):
        ingest(store,req,payload)
    payload=sample_response(req);payload["items"][0]["existing_id"]=str(uuid4())
    with pytest.raises(ValueError,match="既存人物ID"):
        ingest(store,req,payload)
    payload=sample_response(req);payload["items"][0]["fields"].pop("role")
    with pytest.raises(ValueError,match="role"):
        ingest(store,req,payload)


def test_deleted_character_and_concurrent_manual_changes_rejected(assistant):
    store,_=assistant
    character=store.artwork.save_character(dict(name="デモ人物A"),expected_revision=0)
    req=request(assistant)
    payload=sample_response(req);payload["items"][0]["existing_id"]=character["id"]
    proposal=ingest(store,req,payload)
    store.artwork.delete_character(character["id"],expected_revision=1)
    with pytest.raises(StorageError,match="更新・削除"):
        adopt(store,proposal)
    # 最新データの読み込み前に承認した保存にも版番号の照合をかける。
    with pytest.raises(StorageError,match="既存設定が更新"):
        store.adopt(proposal["id"],list(proposal["decisions"]),expected_revision=2,expected_artwork_revision=1,expected_editorial_revision=0)


def test_image_prompts_include_references_and_warn_after_art_changes(assistant):
    from manga.artwork_models import image_context_changed
    store,project=assistant
    character=store.artwork.save_character(dict(name="ルナ"),expected_revision=0)
    reference=store.artwork.add_reference(character["id"],image_bytes(),"正面.png","正面",expected_revision=1)
    req=request(assistant,"directions",targets=targets_for(store.latest()["story"],1,1))
    payload=sample_response(req);payload["items"][0]["fields"]["character_ids"]=[character["id"]]
    p=ingest(store,req,payload);adopt(store,p)
    art=store.artwork.load()
    image=store.artwork.add_panel_image(1,1,image_bytes(),"描画.png",story_version_id=req["story_version_id"],expected_revision=art["revision"])
    art=store.artwork.load();entry=art["panels"]["1:1"]
    assert not image_context_changed(image,entry,art)
    prompts,warnings,_=batch_image_prompts(project,store.latest(),art)
    assert len(prompts)==1 and len(warnings)==23
    assert reference["path"].split("/")[-1] in prompts[0]["prompt"] and "手動で添付" in prompts[0]["prompt"]
    store.artwork.save_direction(1,1,dict(entry["direction"],pose="変更したポーズ"),story_version_id=req["story_version_id"],expected_revision=art["revision"])
    art=store.artwork.load()
    assert image_context_changed(image,art["panels"]["1:1"],art)
    assert store.artwork.image_bytes(reference)==store.artwork.image_bytes(image)


def test_final_journal_write_failure_is_idempotently_resumable(assistant):
    store,_=assistant
    p=ingest(store,request(assistant))
    from manga.storage import write_json_atomic
    def failing(path,data):
        if path==store.journal_path and data["status"]=="done":raise StorageError("完了記録だけ失敗")
        return write_json_atomic(path,data)
    with patch("manga.assistant_repository.write_json_atomic",side_effect=failing):
        with pytest.raises(StorageError):adopt(store,p)
    assert len(store.artwork.load()["characters"])==1 and store.pending()
    store.resume()
    assert store.pending() is None and len(store.artwork.load()["characters"])==1
