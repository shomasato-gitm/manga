import copy
import json
from io import BytesIO
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from manga.artwork_models import direction_fields
from manga.artwork_storage import ArtworkStore
from manga.image_prompts import character_image_prompt, panel_image_prompt
from manga.models import Project
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore


def png(color="red"):
    output = BytesIO()
    Image.new("RGB", (24, 32), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def artwork(tmp_path, manga_project, story):
    ProjectStore(tmp_path).save(manga_project)
    version = StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    return ArtworkStore(tmp_path, manga_project.id), version


def add_character(store, name="ルナ"):
    return store.save_character({"name": name, "hair_color": "銀色", "features": "星形の髪飾り"}, expected_revision=store.load()["revision"])


def add_direction(store, version, character_ids=None, page=1, panel=1):
    return store.save_direction(page, panel, {"character_ids": character_ids or [], "location": "月の図書館", "camera_angle": "斜めからのアップ"},
                                story_version_id=version["id"], expected_revision=store.load()["revision"])


def add_image(store, version, color="red", page=1, panel=1):
    return store.add_panel_image(page, panel, png(color), "ChatGPT.png", story_version_id=version["id"], expected_revision=store.load()["revision"])


def test_character_crud_and_project_isolation(artwork, tmp_path, manga_project):
    store, _ = artwork
    first, second = add_character(store), add_character(store, "ソラ")
    updated = store.save_character(dict(first, clothing="紺色のローブ"), character_id=first["id"], expected_revision=2)
    assert updated["id"] == first["id"]
    assert updated["created_at"] == first["created_at"]
    store.delete_character(second["id"], expected_revision=3)
    saved = ArtworkStore(tmp_path, manga_project.id).load()
    assert saved["characters"][0]["clothing"] == "紺色のローブ"
    assert saved["characters"][1]["deleted_at"]
    assert len([item for item in saved["characters"] if not item["deleted_at"]]) == 1
    assert "ルナ" in store.path.read_text(encoding="utf-8")
    other = Project(title="別の企画", genre="日常", synopsis="学校の話")
    assert ArtworkStore(tmp_path, other.id).load()["characters"] == []


def test_multiple_references_and_primary_choice(artwork):
    store, _ = artwork
    character = add_character(store)
    first = store.add_reference(character["id"], png(), "../../original.png", "正面", expected_revision=1)
    second = store.add_reference(character["id"], png("blue"), "original.png", "斜め", "笑顔", expected_revision=2)
    assert first["original_name"] == "original.png"
    assert first["path"] != second["path"]
    store.select_reference(character["id"], second["id"], expected_revision=3)
    saved = store.load()["characters"][0]
    assert saved["primary_reference_id"] == second["id"]
    assert len(saved["references"]) == 2
    assert store.image_bytes(first) != store.image_bytes(second)
    store.delete_character(character["id"], expected_revision=4)
    assert store.image_bytes(first) == png()
    assert len(store.load()["characters"][0]["references"]) == 2


def test_directions_and_images_do_not_modify_story_or_project(artwork, tmp_path, manga_project):
    store, version = artwork
    project_before = (tmp_path / f"{manga_project.id}.json").read_bytes()
    story_before = store.story_store.path.read_bytes()
    character = add_character(store)
    add_direction(store, version, [character["id"]])
    first, second = add_image(store, version), add_image(store, version, "blue")
    store.select_panel_image(1, 1, first["id"], expected_revision=4)
    store.select_panel_image(1, 1, second["id"], expected_revision=5)
    entry = store.load()["panels"]["1:1"]
    assert entry["selected_image_id"] == second["id"]
    assert [item["image_id"] for item in entry["selection_history"]] == [first["id"], second["id"]]
    assert len(entry["candidates"]) == 2
    assert all(store.image_bytes(image) for image in entry["candidates"])
    assert entry["direction"]["location"] == "月の図書館"
    assert store.story_store.path.read_bytes() == story_before
    assert (tmp_path / f"{manga_project.id}.json").read_bytes() == project_before


def test_candidate_preserves_registration_context(artwork):
    store, version = artwork
    character = add_character(store)
    add_direction(store, version, [character["id"]])
    image = add_image(store, version)
    old_context = copy.deepcopy(image["context"])
    store.save_style("カラー水彩", expected_revision=3)
    store.save_character({"name": "ルナ", "hair_color": "黒"}, character_id=character["id"], expected_revision=4)
    assert store.load()["panels"]["1:1"]["candidates"][0]["context"] == old_context


def test_reference_and_panel_prompts_include_settings(artwork, manga_project, story):
    store, version = artwork
    character = add_character(store)
    image = store.add_reference(character["id"], png(), "original.png", "正面", expected_revision=1)
    add_direction(store, version, [character["id"]])
    data = store.load()
    character_prompt = character_image_prompt(data["characters"][0], data["style"], "全身", "笑顔")
    assert "全身" in character_prompt and "笑顔" in character_prompt
    assert "銀色" in character_prompt and "星形の髪飾り" in character_prompt
    assert f"{image['id']}.png" in character_prompt and "基準画像" in character_prompt
    prompt = panel_image_prompt(manga_project, story, story["pages"][0], story["pages"][0]["panels"][0], data)
    for content in ("ルナ", "銀色", "月の図書館", "斜めからのアップ", data["style"], "吹き出し", "描き込まない", "統一"):
        assert content in prompt


def test_deleted_character_cannot_be_used_in_new_prompt(artwork, manga_project, story):
    store, version = artwork
    character = add_character(store)
    add_direction(store, version, [character["id"]])
    store.delete_character(character["id"], expected_revision=2)
    with pytest.raises(ValueError, match="削除済み"):
        panel_image_prompt(manga_project, story, story["pages"][0], story["pages"][0]["panels"][0], store.load())
    with pytest.raises(ValueError, match="削除済み"):
        add_direction(store, version, [character["id"]])
    add_direction(store, version, [])
    assert store.load()["panels"]["1:1"]["direction"]["character_ids"] == []


def test_story_change_requires_direction_review_and_keeps_images(artwork, manga_project, story):
    store, version = artwork
    add_direction(store, version)
    image = add_image(store, version)
    changed = copy.deepcopy(story)
    changed["pages"][0]["panels"][0]["description"] = "新しい描写"
    new_version = store.story_store.save(changed, expected_version_id=version["id"])
    with pytest.raises(ValueError, match="変更"):
        panel_image_prompt(manga_project, changed, changed["pages"][0], changed["pages"][0]["panels"][0], store.load())
    with pytest.raises(StorageError, match="ストーリーが更新"):
        add_direction(store, version)
    add_direction(store, new_version)
    assert store.load()["panels"]["1:1"]["direction"]["source_panel"]["description"] == "新しい描写"
    assert store.image_bytes(image)
    assert store.load()["panels"]["1:1"]["candidates"][0]["source_panel"]["description"] != "新しい描写"


def test_foreign_candidate_cannot_be_selected(artwork):
    store, version = artwork
    image = add_image(store, version)
    with pytest.raises(ValueError, match="このコマ"):
        store.select_panel_image(1, 2, image["id"], expected_revision=1)


def test_failed_image_save_preserves_existing_files_and_metadata(artwork):
    store, version = artwork
    first = add_image(store, version)
    previous = store.path.read_bytes()
    before_files = set(store.root.rglob("*.png"))
    with patch("manga.storage.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StorageError):
            add_image(store, version, "blue")
    assert store.path.read_bytes() == previous
    assert set(store.root.rglob("*.png")) == before_files
    assert store.image_bytes(first)


def test_stale_revision_cannot_overwrite_other_changes(artwork):
    store, _ = artwork
    add_character(store)
    with pytest.raises(StorageError, match="更新"):
        store.save_style("水彩", expected_revision=0)
    assert len(store.load()["characters"]) == 1


def test_concurrent_uploads_do_not_lose_candidates(artwork):
    store, version = artwork

    def upload(color):
        try:
            store.add_panel_image(1, 1, png(color), "image.png", story_version_id=version["id"], expected_revision=0)
            return "saved"
        except StorageError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(upload, ["red", "blue"]))
    assert sorted(results) == ["conflict", "saved"]
    assert len(store.load()["panels"]["1:1"]["candidates"]) == 1
    assert len(list(store.root.rglob("*.png"))) == 1


@pytest.mark.parametrize("damage", [
    lambda data: data.update(schema_version=99),
    lambda data: data.update(project_id="wrong"),
    lambda data: data["panels"]["1:1"]["candidates"][0].update(path="../../outside.png"),
    lambda data: data["panels"]["1:1"].update(selected_image_id="unknown"),
    lambda data: data["panels"]["1:1"]["candidates"][0].update(width=-20, height=-20),
    lambda data: data["panels"]["1:1"]["candidates"][0]["context"].update(characters=None),
])
def test_corrupt_artwork_not_overwritten(artwork, damage):
    store, version = artwork
    image = add_image(store, version)
    data = store.load()
    damage(data)
    store.path.write_text(json.dumps(data), encoding="utf-8")
    previous = store.path.read_bytes()
    with pytest.raises(StorageError, match="形式が不正"):
        store.save_style("水彩", expected_revision=1)
    assert store.path.read_bytes() == previous
    assert (store.root / image["path"]).exists()


def test_image_paths_cannot_escape_project(artwork, tmp_path):
    store, version = artwork
    image = add_image(store, version)
    with pytest.raises(StorageError, match="保存先"):
        store.image_bytes(dict(image, path="../../private.png"))
    path = store.root / image["path"]
    path.unlink()
    external = tmp_path / "external.png"
    external.write_bytes(png())
    path.symlink_to(external)
    with pytest.raises(StorageError, match="外側"):
        store.image_bytes(image)


def test_image_writes_reject_symlink_outside_project(artwork, tmp_path):
    store, version = artwork
    outside = tmp_path / "outside-images"
    outside.mkdir()
    (store.root / "images").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError, match="外側"):
        add_image(store, version)
    assert list(outside.iterdir()) == []
    assert store.load()["panels"] == {}


def test_missing_primary_reference_is_reported_as_corrupt_settings(artwork):
    store, _ = artwork
    character = add_character(store)
    store.add_reference(character["id"], png(), "ref.png", "正面", expected_revision=1)
    data = store.load()
    data["characters"][0]["primary_reference_id"] = None
    store.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StorageError, match="形式が不正"):
        store.load()


@pytest.mark.parametrize("ids", [["../../bad"], [None], [["nested"]]])
def test_invalid_character_selection_is_rejected(ids):
    with pytest.raises(ValueError):
        direction_fields({"character_ids": ids})
