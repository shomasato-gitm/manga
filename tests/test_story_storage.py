import copy
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore


def test_history_import_edit_restore_and_restart(tmp_path, manga_project, story):
    project_store = ProjectStore(tmp_path)
    saved_project = project_store.save(manga_project)
    store = StoryStore(tmp_path, saved_project.id)
    assert store.load_history() == []
    first = store.save(story, expected_version_id=None)
    edited = copy.deepcopy(story)
    edited["pages"][2]["panels"][0]["description"] = "本から光があふれる。"
    second = store.save(edited, expected_version_id=first["id"], action="ページ編集")
    third = store.restore(first["id"], expected_version_id=second["id"])
    restarted = StoryStore(tmp_path, saved_project.id)
    history = restarted.load_history()
    assert len(history) == 3
    assert history[0]["story"] == history[2]["story"] == story
    assert history[1]["story"] == edited
    assert third["restored_from"] == first["id"]
    assert history[0]["id"] != history[2]["id"]
    assert project_store.load(saved_project.id) == saved_project
    assert project_store.list_projects()[0] == [saved_project]


def test_save_copies_input(tmp_path, manga_project, story):
    store = StoryStore(tmp_path, manga_project.id)
    version = store.save(story, expected_version_id=None)
    story["pages"][0]["summary"] = "後から変更"
    assert store.load_history()[0] == version


def test_projects_are_isolated(tmp_path, manga_project, story):
    from manga.models import Project

    first = StoryStore(tmp_path, manga_project.id)
    second = StoryStore(tmp_path, Project(title="別企画", genre="SF", synopsis="宇宙へ。").id)
    first.save(story, expected_version_id=None)
    assert second.load_history() == []


def test_stale_write_preserves_history(tmp_path, manga_project, story):
    store = StoryStore(tmp_path, manga_project.id)
    first = store.save(story, expected_version_id=None)
    with pytest.raises(StorageError, match="更新されました"):
        store.save(story, expected_version_id=None)
    assert store.load_history() == [first]


def test_concurrent_writes_do_not_lose_versions(tmp_path, manga_project, story):
    def save():
        try:
            StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
            return "saved"
        except StorageError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: save(), range(2)))
    assert sorted(results) == ["conflict", "saved"]
    assert len(StoryStore(tmp_path, manga_project.id).load_history()) == 1


def test_failed_save_keeps_all_previous_versions(tmp_path, manga_project, story):
    store = StoryStore(tmp_path, manga_project.id)
    first = store.save(story, expected_version_id=None)
    previous = store.path.read_bytes()
    with patch("manga.storage.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StorageError):
            store.save(story, expected_version_id=first["id"])
    assert store.path.read_bytes() == previous
    assert list(store.directory.glob("*.tmp")) == []


@pytest.mark.parametrize("damage", [
    lambda data: data.update(schema_version=99),
    lambda data: data.update(project_id="wrong-project"),
    lambda data: data.update(versions=[]),
    lambda data: data["versions"].append(data["versions"][0]),
    lambda data: data["versions"][0].update(story={}),
    lambda data: data["versions"][0].update(saved_at="invalid"),
    lambda data: data["versions"][0].update(action="復元", restored_from="missing"),
])
def test_corrupt_history_cannot_be_overwritten(tmp_path, manga_project, story, damage):
    store = StoryStore(tmp_path, manga_project.id)
    first = store.save(story, expected_version_id=None)
    data = json.loads(store.path.read_text())
    damage(data)
    store.path.write_text(json.dumps(data), encoding="utf-8")
    previous = store.path.read_bytes()
    with pytest.raises(StorageError, match="履歴の形式"):
        store.save(story, expected_version_id=first["id"])
    assert store.path.read_bytes() == previous


def test_project_id_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        StoryStore(tmp_path, "../../outside")
