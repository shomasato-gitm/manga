import json
from pathlib import Path

import pytest

from manga.prompts import generation_prompt, improvement_prompt
from manga.story import MAX_IMPORT_BYTES, StoryValidationError, decode_story_file, normalize_story, parse_story


def test_exact_eight_pages_and_twenty_four_panels(story):
    result = parse_story(json.dumps(story, ensure_ascii=False), 8)
    assert result.story == story
    assert result.warnings == []
    assert sum(len(page["panels"]) for page in result.story["pages"]) == 24


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "評価：構成を改善しました。\n```JSON\n{}\n```\nご確認ください。", "\ufeff{}"])
def test_chatgpt_fences_and_bom(story, wrapper):
    raw = wrapper.replace("{}", json.dumps(story, ensure_ascii=False))
    assert parse_story(raw, 8).story == story


def test_japanese_keys_missing_numbers_and_dialogue_lists():
    raw = {"ストーリー": {"タイトル": "星の手紙", "あらすじ": "星へ手紙を届ける。", "ページ": [{
        "概要": "手紙を見つける。",
        "コマ": [
            {"描写": "少年が手紙を拾う。", "セリフ": [{"話者": "少年", "セリフ": "誰の手紙？"}]},
            {"場面": "夜空が輝く。", "セリフ": ["わあ！", "きれい。"]},
            {"scene": "封筒が光る。"},
        ],
    }]}}
    result = normalize_story(raw, 1)
    page = result.story["pages"][0]
    assert page["page"] == 1
    assert page["panels"][0]["panel"] == 1
    assert page["panels"][0]["dialogue"] == "少年：誰の手紙？"
    assert page["panels"][1]["dialogue"] == "わあ！\nきれい。"
    assert page["panels"][2]["narration"] == ""


def test_string_numbers_and_missing_title(story):
    del story["title"]
    for page in story["pages"]:
        page["page"] = str(page["page"])
        for panel in page["panels"]:
            panel["panel"] = str(panel["panel"])
    imported = normalize_story(story, 8, "設定のタイトル")
    assert imported.story["title"] == "設定のタイトル"
    assert imported.story["pages"][0]["page"] == 1


def test_unknown_keys_and_nonstandard_panel_counts_warn(story):
    story["characters"] = ["司書"]
    story["pages"][0]["panels"].pop()
    story["pages"][1]["panels"][0]["camera"] = "アップ"
    result = normalize_story(story, 8)
    assert len(result.warnings) == 3
    assert "characters" not in result.story
    assert "camera" not in result.story["pages"][1]["panels"][0]


@pytest.mark.parametrize("raw, message", [
    ("", "貼り付け"), ("{", "行"), ('{"title": "a",}', "カンマ"),
    ('{"title":"a","title":"b"}', "重複"), ('{"value":NaN}', "NaN"),
    ('[]', "オブジェクト"), ('{"pages":[]}', "タイトル"),
    ('```json\n{}\n```\n```json\n{}\n```', "複数"),
])
def test_bad_json_has_readable_error(raw, message):
    with pytest.raises(StoryValidationError, match=message):
        parse_story(raw, 8)


@pytest.mark.parametrize("change, message", [
    (lambda data: data["pages"].pop(), "8ページ"),
    (lambda data: data["pages"][0].update(page=2), "ページ番号"),
    (lambda data: data["pages"][0].update(page=True), "ページ番号"),
    (lambda data: data["pages"][0].update(panels=[]), "1〜6"),
    (lambda data: data["pages"][0].update(panels="not a list"), "配列"),
    (lambda data: data["pages"][0]["panels"][1].update(panel=1), "コマ番号"),
    (lambda data: data["pages"][0]["panels"][0].update(description=""), "1ページ目・1コマ目の描写"),
    (lambda data: data["pages"][0]["panels"][0].update(description=42), "文字列"),
    (lambda data: data["pages"][0]["panels"][0].update(dialogue={"text": "hello"}), "セリフ"),
    (lambda data: data.update(title="a" * 101), "100文字"),
    (lambda data: data.update(schema_version=2), "schema_version"),
    (lambda data: data.update(タイトル="矛盾するタイトル"), "値が異なります"),
])
def test_invalid_structure_identifies_location(story, change, message):
    change(story)
    with pytest.raises(StoryValidationError, match=message):
        normalize_story(story, 8)


def test_utf8_file_import_uses_same_parser(story):
    content = json.dumps(story, ensure_ascii=False).encode("utf-8-sig")
    assert parse_story(decode_story_file(content), 8).story == story


@pytest.mark.parametrize("content, message", [(b"\xff", "UTF-8"), (b"x" * (MAX_IMPORT_BYTES + 1), "1MB")])
def test_bad_upload(content, message):
    with pytest.raises(StoryValidationError, match=message):
        decode_story_file(content)


def test_oversized_paste():
    with pytest.raises(StoryValidationError, match="1MB"):
        parse_story("あ" * (MAX_IMPORT_BYTES // 3 + 1))


def test_optional_nulls_are_empty_strings(story):
    story["summary"] = None
    story["pages"][0]["panels"][0].update(dialogue=None, narration=None)
    normalized = normalize_story(story, 8).story
    assert normalized["summary"] == ""
    assert normalized["pages"][0]["panels"][0]["dialogue"] == ""


def test_large_number_string_has_location_error(story):
    story["pages"][0]["page"] = "1" * 5000
    with pytest.raises(StoryValidationError, match="ページ番号"):
        normalize_story(story, 8)


def test_example_is_a_complete_story():
    sample = Path(__file__).resolve().parents[1] / "examples" / "story_8pages.json"
    result = parse_story(decode_story_file(sample.read_bytes()), 8)
    assert len(result.story["pages"]) == 8
    assert result.warnings == []


def test_literal_code_fences_in_dialogue_are_preserved(story):
    story["pages"][0]["panels"][0]["dialogue"] = "プログラマー：```python\nprint('こんにちは')\n```"
    assert parse_story(json.dumps(story), 8).story == story


def test_excessive_json_number_has_readable_error():
    with pytest.raises(StoryValidationError, match="数値"):
        parse_story('{"big":' + "1" * 5000 + "}")


def test_prompts_contain_settings_and_complete_json(manga_project, story):
    prompt = generation_prompt(manga_project)
    assert manga_project.title in prompt and manga_project.genre in prompt and manga_project.synopsis in prompt
    assert "全8ページ" in prompt and "各ページ3コマ" in prompt
    template = json.loads(prompt[prompt.index('{\n  "schema_version"'):])
    assert len(template["pages"]) == 8
    manga_project.page_count = 12
    assert "全12ページ" in generation_prompt(manga_project)
    improvement = improvement_prompt(manga_project, story, "結末を明るく")
    assert "結末を明るく" in improvement
    assert story["pages"][-1]["panels"][-1]["description"] in improvement
    assert "一貫性" in improvement
