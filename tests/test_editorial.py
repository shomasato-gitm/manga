import copy
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import pytest

from manga.artwork_storage import ArtworkStore
from manga.editorial_models import CRITERIA, MODES, parse_analysis, parse_evaluation, reference_fields
from manga.editorial_prompts import analysis_prompt, analysis_template, evaluation_prompt, evaluation_template
from manga.editorial_storage import EditorialStore, current_analysis, generation_references
from manga.editorial_workflow import evaluation_for_version, version_context, workflow_status
from manga.prompts import generation_prompt, improvement_prompt
from manga.storage import ProjectStore, StorageError
from manga.story_comparison import compare_stories
from manga.story_storage import StoryStore


@pytest.fixture
def editorial(tmp_path, manga_project, story):
    ProjectStore(tmp_path).save(manga_project)
    version = StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    return EditorialStore(tmp_path, manga_project.id), version


def reference(store, title="参考作品A"):
    return store.save_reference({"title": title, "medium": "映画", "techniques": "目的の提示を遅らせる",
                                 "scene_summary": "登録した短い観察だけ", "avoid": "固有の舞台は使わない"}, expected_revision=store.load()["revision"])


def evaluation(store, version, points=5):
    payload = evaluation_template(version["id"])
    for item in payload["criteria"].values():
        item["score"] = points
    return store.save_evaluation(version["id"], json.dumps(payload), expected_revision=store.load()["revision"])


def test_reference_crud_preserves_analysis_history_and_original_data(editorial, tmp_path, manga_project):
    store, version = editorial
    original_story = store.stories.path.read_bytes()
    original_project = (tmp_path / f"{manga_project.id}.json").read_bytes()
    first, second = reference(store), reference(store, "参考作品B")
    payload = analysis_template(first["id"])
    payload["aspects"]["opening"].update(status="ユーザー要約に基づく", abstract_technique="情報を段階的に開示")
    analysis = store.save_analysis(first["id"], json.dumps(payload), expected_revision=2)
    assert current_analysis(store.load(), first) == analysis
    used = generation_references(store.load(), [first["id"]])
    assert used[0]["techniques"] == [first["techniques"], "情報を段階的に開示"]
    assert "scene_summary" not in used[0]
    changed = store.save_reference(dict(first, notes="登録内容を修正"), reference_id=first["id"], expected_revision=3)
    assert changed["version"] == 2
    assert current_analysis(store.load(), changed) is None
    with pytest.raises(ValueError, match="更新"):
        store.save_analysis(first["id"], json.dumps(payload), expected_revision=4)
    store.delete_reference(second["id"], expected_revision=4)
    assert len(store.load()["references"]) == 2
    assert store.load()["references"][1]["deleted_at"]
    assert len(store.load()["analyses"]) == 1
    assert store.stories.path.read_bytes() == original_story
    assert (tmp_path / f"{manga_project.id}.json").read_bytes() == original_project
    assert EditorialStore(tmp_path, str(uuid4())).load()["evaluations"] == []


def test_reference_validation_and_wrong_target(editorial):
    store, _ = editorial
    with pytest.raises(ValueError, match="作品名"):
        reference_fields({"medium": "映画"})
    with pytest.raises(ValueError, match="出典URL"):
        reference_fields({"title": "a", "medium": "b", "source_url": "file:///private"})
    ref = reference(store)
    with pytest.raises(ValueError, match="reference_id"):
        parse_analysis(json.dumps(analysis_template(str(uuid4()))), ref["id"])
    bad = analysis_template(ref["id"])
    bad["aspects"]["opening"]["status"] = "事実"
    with pytest.raises(ValueError, match="確認状態"):
        parse_analysis(json.dumps(bad), ref["id"])
    with pytest.raises(ValueError, match="3作品"):
        generation_references(store.load(), [ref["id"]] * 4)


def test_prompts_use_summaries_uncertainty_and_abstract_techniques(editorial, manga_project):
    store, version = editorial
    ref = reference(store)
    prompt = analysis_prompt(ref)
    for expected in ("不明", "断定せず", "登録した短い観察だけ", "reference_version", "伏線と回収"):
        assert expected in prompt
    generated = generation_prompt(manga_project, generation_references(store.load(), [ref["id"]]))
    assert "目的の提示を遅らせる" in generated
    assert "登録した短い観察だけ" not in generated
    assert "固有の展開・場面・セリフを再現しない" in generated
    assert "schema_version" in generation_prompt(manga_project)
    assert version["id"] in evaluation_prompt(manga_project, version)


def test_evaluation_is_bound_to_version_and_total_is_recomputed(editorial):
    store, version = editorial
    payload = evaluation_template(version["id"])
    payload["total_score"] = 100
    for item in payload["criteria"].values():
        item["score"] = "7.5"
    record = store.save_evaluation(version["id"], "```json\n" + json.dumps(payload) + "\n```", expected_revision=0)
    assert record["payload"]["total_score"] == 75
    assert record["payload"]["criteria"]["opening"]["targets"] == [{"page": 1, "panel": 1}]
    assert len(store.load()["evaluations"]) == 1
    with pytest.raises(ValueError, match="story_version_id"):
        store.save_evaluation(version["id"], json.dumps(evaluation_template(str(uuid4()))), expected_revision=1)


@pytest.mark.parametrize("value", [-1, 11, True, "NaN", "Infinity", None, [], "bad"])
def test_invalid_scores(editorial, value):
    _, version = editorial
    payload = evaluation_template(version["id"])
    payload["criteria"]["opening"]["score"] = value
    with pytest.raises(ValueError, match="点数"):
        parse_evaluation(json.dumps(payload), version["id"], version["story"])


@pytest.mark.parametrize("change, message", [
    (lambda data: data["criteria"].pop("ending"), "結末"),
    (lambda data: data["criteria"]["opening"].update(targets=[{"page": 99, "panel": 1}]), "ページ"),
    (lambda data: data["criteria"]["opening"].update(targets=[{"page": 1, "panel": 6}]), "対象コマ"),
    (lambda data: data["criteria"]["opening"].update(reason=""), "改善理由"),
    (lambda data: data.update(schema_version=2), "schema_version"),
])
def test_invalid_evaluation_does_not_write(editorial, change, message):
    store, version = editorial
    payload = evaluation_template(version["id"])
    change(payload)
    with pytest.raises(ValueError, match=message):
        store.save_evaluation(version["id"], json.dumps(payload), expected_revision=0)
    assert not store.path.exists()


@pytest.mark.parametrize("mode", MODES)
def test_improvement_reevaluation_comparison_and_adoption(editorial, mode):
    store, version = editorial
    original = copy.deepcopy(version["story"])
    first_eval = evaluation(store, version)
    changed = copy.deepcopy(original)
    changed["pages"][0]["panels"][0].update(dialogue="ルナ：新しいセリフ", description="新しい描写")
    improved, warnings = store.save_improvement(json.dumps(changed), version["id"], first_eval["id"], mode, ["opening"], [{"page": 1, "panel": 1}],
                                               "導入を明確にする", expected_version_id=version["id"])
    assert warnings == []
    assert improved["revision_context"]["base_version_id"] == version["id"]
    assert store.stories.load_history()[0]["story"] == original
    second_eval = evaluation(store, improved, 8)
    differences = compare_stories(original, changed)
    assert len(differences) == 2
    assert differences[0]["page"] == differences[0]["panel"] == 1
    assert {difference["field"] for difference in differences} == {"description", "dialogue"}
    assert first_eval["payload"]["total_score"] == 50
    assert second_eval["payload"]["total_score"] == 80
    adopted = store.stories.restore(version["id"], expected_version_id=improved["id"])
    adopted_eval, inherited = evaluation_for_version(store.load(), adopted, store.stories.load_history())
    assert inherited and adopted_eval == first_eval
    assert len(store.stories.load_history()) == 3
    assert version_context(improved, store.stories.load_history())["reason"] == "導入を明確にする"
    assert any(row["工程"] == "再評価" and "1版" in row["状態"] for row in workflow_status(store.load(), store.stories.load_history())[0])


def test_partial_improvement_rejects_outside_scope(editorial):
    store, version = editorial
    record = evaluation(store, version)
    changed = copy.deepcopy(version["story"])
    changed["pages"][1]["panels"][0]["dialogue"] = "範囲外の変更"
    with pytest.raises(ValueError, match="指定範囲外"):
        store.save_improvement(json.dumps(changed), version["id"], record["id"], MODES[1], ["dialogue"], [{"page": 1, "panel": 1}], "理由", expected_version_id=version["id"])
    assert len(store.stories.load_history()) == 1
    with pytest.raises(ValueError, match="1件以上"):
        store.save_improvement(json.dumps(changed), version["id"], record["id"], MODES[1], ["dialogue"], [], "理由", expected_version_id=version["id"])


def test_comparison_records_page_and_panel_additions_and_metadata(story):
    changed = copy.deepcopy(story)
    changed["title"] = "変更後"
    changed["pages"][0]["summary"] = "新しい概要"
    changed["pages"][0]["panels"].pop()
    changed["pages"].pop()
    differences = compare_stories(story, changed)
    assert {item["field"] for item in differences} == {"title", "page_summary", "panel", "page"}
    assert compare_stories(story, copy.deepcopy(story)) == []


def test_finalization_detects_every_story_edit_without_touching_artwork(editorial, tmp_path, manga_project):
    store, version = editorial
    artwork = ArtworkStore(tmp_path, manga_project.id)
    artwork.save_style("水彩", expected_revision=0)
    original_art = artwork.path.read_bytes()
    store.finalize(expected_version_id=version["id"], expected_revision=0)
    assert workflow_status(store.load(), store.stories.load_history())[2]
    changed = copy.deepcopy(version["story"])
    changed["pages"][0]["panels"][0]["description"] = "確定後に変更"
    new_version = store.stories.save(changed, expected_version_id=version["id"], action="ページ編集")
    assert workflow_status(store.load(), store.stories.load_history())[1]
    with pytest.raises(StorageError, match="更新"):
        store.finalize(expected_version_id=version["id"], expected_revision=1)
    store.finalize(expected_version_id=new_version["id"], expected_revision=1)
    assert workflow_status(store.load(), store.stories.load_history())[2]
    assert len(store.load()["finalizations"]) == 2
    assert artwork.path.read_bytes() == original_art


def test_atomic_failure_and_stale_save_preserve_records(editorial):
    store, version = editorial
    evaluation(store, version)
    previous = store.path.read_bytes()
    with patch("manga.storage.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StorageError):
            evaluation(store, version, 8)
    assert store.path.read_bytes() == previous
    with pytest.raises(StorageError, match="更新"):
        store.save_reference({"title": "a", "medium": "小説"}, expected_revision=0)
    assert store.path.read_bytes() == previous


def test_concurrent_evaluations_do_not_lose_records(editorial):
    store, version = editorial

    def save():
        try:
            store.save_evaluation(version["id"], json.dumps(evaluation_template(version["id"])), expected_revision=0)
            return "saved"
        except StorageError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: save(), range(2)))
    assert sorted(results) == ["conflict", "saved"]
    assert len(store.load()["evaluations"]) == 1


@pytest.mark.parametrize("change", [
    lambda data: data.update(schema_version=99),
    lambda data: data["evaluations"][0]["payload"].update(total_score=100),
    lambda data: data["evaluations"].append(data["evaluations"][0]),
    lambda data: data.update(project_id="other"),
])
def test_corrupt_editorial_data_not_overwritten(editorial, change):
    store, version = editorial
    evaluation(store, version)
    data = store.load()
    change(data)
    store.path.write_text(json.dumps(data), encoding="utf-8")
    previous = store.path.read_bytes()
    with pytest.raises(StorageError, match="形式が不正"):
        evaluation(store, version)
    assert store.path.read_bytes() == previous


def test_repeated_improvement_and_wrong_evaluation_cannot_be_mixed(editorial):
    store, first = editorial
    first_eval = evaluation(store, first)
    changed = copy.deepcopy(first["story"])
    changed["pages"][0]["panels"][0]["dialogue"] = "第2稿"
    second, _ = store.save_improvement(json.dumps(changed), first["id"], first_eval["id"], MODES[0], ["dialogue"], [], "自然なセリフへ", expected_version_id=first["id"])
    with pytest.raises(ValueError, match="一致"):
        store.save_improvement(json.dumps(changed), second["id"], first_eval["id"], MODES[0], ["dialogue"], [], "理由", expected_version_id=second["id"])
    second_eval = evaluation(store, second, 7)
    changed["pages"][0]["panels"][0]["dialogue"] = "第3稿"
    third, _ = store.save_improvement(json.dumps(changed), second["id"], second_eval["id"], MODES[2], ["dialogue"], [], "さらに簡潔に", expected_version_id=second["id"])
    assert len(store.stories.load_history()) == 3
    assert third["revision_context"]["base_version_id"] == second["id"]
    assert evaluation_for_version(store.load(), third, store.stories.load_history()) == (None, False)


@pytest.mark.parametrize("mode", MODES)
def test_improvement_prompt_preserves_scope_and_characters(editorial, manga_project, mode):
    store, version = editorial
    record = evaluation(store, version)
    prompt = improvement_prompt(manga_project, version["story"], evaluation=record["payload"], mode=mode, criteria=["dialogue"],
                                targets=[{"page": 1, "panel": 1}], characters=[{"name": "ルナ", "clothing": "青いローブ"}])
    assert mode in prompt and "青いローブ" in prompt
    assert "元のテーマ・世界観" in prompt and "全ページ" in prompt
    if mode == MODES[1]:
        assert "範囲外" in prompt


def test_corrupt_improvement_provenance_is_not_overwritten(editorial):
    store, version = editorial
    record = evaluation(store, version)
    improved, _ = store.save_improvement(json.dumps(version["story"]), version["id"], record["id"], MODES[0], ["dialogue"], [], "理由", expected_version_id=version["id"])
    history = json.loads(store.stories.path.read_text())
    history["versions"][-1]["revision_context"]["base_version_id"] = str(uuid4())
    store.stories.path.write_text(json.dumps(history), encoding="utf-8")
    before = store.stories.path.read_bytes()
    with pytest.raises(StorageError, match="履歴の形式"):
        store.stories.save(improved["story"], expected_version_id=improved["id"])
    assert store.stories.path.read_bytes() == before
