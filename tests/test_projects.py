import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from manga.models import BudgetState, Project, WorkflowState
from manga.storage import ProjectStore, StorageError


def project(**changes):
    values = dict(title="星の配達人", genre="SF", synopsis="新人配達人が月で手紙を届ける。", page_count=8)
    values.update(changes)
    return Project(**values)


def test_japanese_json_round_trip(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    saved = store.save(project())
    raw = (store.directory / f"{saved.id}.json").read_text(encoding="utf-8")
    assert "星の配達人" in raw
    assert json.loads(raw)["schema_version"] == 1
    assert store.load(saved.id) == saved


def test_edit_keeps_identity_and_future_state(tmp_path):
    store = ProjectStore(tmp_path)
    original = store.save(project(
        workflow=WorkflowState(current_step="story", completed_steps=["project"], artifacts={"story": "story.json"}),
        budget=BudgetState(limit_usd="5.00", spent_usd="0.10"),
    ))
    saved = store.save(replace(original, title="新しいタイトル"))
    assert saved.id == original.id
    assert saved.created_at == original.created_at
    assert saved.updated_at >= original.updated_at
    assert saved.workflow == original.workflow
    assert saved.budget == original.budget
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert store.load(saved.id) == saved


def test_same_title_creates_separate_projects(tmp_path):
    store = ProjectStore(tmp_path)
    first, second = store.save(project()), store.save(project())
    projects, warnings = store.list_projects()
    assert [p.id for p in projects] == [second.id, first.id]
    assert warnings == []


@pytest.mark.parametrize("changes", [
    {"title": "  "}, {"title": "a" * 101}, {"genre": ""}, {"synopsis": ""},
    {"page_count": 0}, {"page_count": 101}, {"page_count": 1.5}, {"page_count": True},
    {"id": "../../outside"}, {"schema_version": 2}, {"created_at": "invalid"},
    {"budget": BudgetState(spent_usd=None)}, {"budget": BudgetState(limit_usd="NaN")},
    {"budget": BudgetState(spent_usd="-0.01")},
])
def test_invalid_input(changes):
    with pytest.raises(ValueError):
        project(**changes)


def test_empty_directory_is_valid(tmp_path):
    assert ProjectStore(tmp_path / "not-created").list_projects() == ([], [])


@pytest.mark.parametrize("content", ["{broken", "[]", '{"title": "incomplete"}'])
def test_corrupt_files_do_not_hide_valid_projects(tmp_path, content):
    store = ProjectStore(tmp_path)
    valid = store.save(project())
    broken_id = project().id
    (tmp_path / f"{broken_id}.json").write_text(content, encoding="utf-8")
    projects, warnings = store.list_projects()
    assert projects == [valid]
    assert len(warnings) == 1


def test_invalid_file_name_is_reported(tmp_path):
    (tmp_path / "unknown.json").write_text("{}", encoding="utf-8")
    assert len(ProjectStore(tmp_path).list_projects()[1]) == 1


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        ProjectStore(tmp_path).load("../outside")


def test_missing_file_is_user_facing_error(tmp_path):
    with pytest.raises(StorageError):
        ProjectStore(tmp_path).load(project().id)


def test_file_id_must_match_content(tmp_path):
    saved = project()
    other_id = project().id
    (tmp_path / f"{other_id}.json").write_text(json.dumps(saved.to_dict()), encoding="utf-8")
    with pytest.raises(StorageError):
        ProjectStore(tmp_path).load(other_id)


def test_atomic_save_failure_preserves_previous_json(tmp_path):
    store = ProjectStore(tmp_path)
    original = store.save(project())
    with patch("manga.storage.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StorageError):
            store.save(replace(original, title="失敗する更新"))
    assert store.load(original.id) == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_unsupported_schema_rejected(tmp_path):
    original = project()
    data = original.to_dict()
    data["schema_version"] = 99
    (tmp_path / f"{original.id}.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StorageError):
        ProjectStore(tmp_path).load(original.id)
