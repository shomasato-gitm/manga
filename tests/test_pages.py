"""ページ合成の読み順、文字・画像の保護、出力内容を実データで確認する。"""

import copy
import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from manga.artwork_storage import ArtworkStore
from manga.japanese_text import TextLayoutError, check_glyphs, draw_fitted_text, find_japanese_font, load_japanese_font
from manga.page_exports import export_book, export_vertical, page_png
from manga.page_layout import DEFAULT_SETTINGS, TEMPLATES, default_page, dimensions, effective_page, template_frames, validate_frames, validate_page, validate_settings
from manga.page_render import RenderCache, pixel_frames, render_page
from manga.page_storage import PageStore
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore


@pytest.fixture(scope="module")
def font():
    # 開発環境は日本語フォント必須。Macではヒラギノ、LinuxではNoto CJK。
    return find_japanese_font()


@pytest.fixture
def composition(tmp_path, manga_project, story):
    ProjectStore(tmp_path).save(manga_project)
    latest = StoryStore(tmp_path, manga_project.id).save(story, expected_version_id=None)
    store = PageStore(tmp_path, manga_project.id)
    data = store.load()
    data["settings"]["width"] = 600
    images = ArtworkStore(tmp_path, manga_project.id)
    return store, data, images, latest


def quiet_page(data, latest, number=1, template="grid"):
    page = latest["story"]["pages"][number - 1]
    config = default_page(data["project_id"], page, latest["id"])
    config["template"], config["bubbles"] = template, []
    data["pages"][str(number)] = config
    return config


def upload(images, latest, color, panel=1, page=1, content=None):
    if content is None:
        out = BytesIO()
        Image.new("RGB", (100, 100), color).save(out, "PNG")
        content = out.getvalue()
    candidate = images.add_panel_image(page, panel, content, "元画像.png", story_version_id=latest["id"], expected_revision=images.load()["revision"])
    images.select_panel_image(page, panel, candidate["id"], expected_revision=images.load()["revision"])
    return candidate


@pytest.mark.parametrize("count", range(1, 7))
@pytest.mark.parametrize("template", TEMPLATES)
def test_templates_are_inside_non_overlapping_and_rtl(template, count):
    for settings in (DEFAULT_SETTINGS, dict(DEFAULT_SETTINGS, margin=10, gutter=5, aspect="square")):
        frames = template_frames(template, count, settings)
        assert validate_frames(frames, count) == frames
        if template == "grid" and count > 1:
            assert frames[0]["x"] > frames[1]["x"]
        if template == "hero_top" and count == 3:
            assert frames[0]["y"] < frames[1]["y"] == frames[2]["y"]
            assert frames[1]["x"] > frames[2]["x"]
        if template == "hero_right" and count > 1:
            assert frames[0]["x"] > frames[1]["x"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 101])
def test_reject_bad_coordinates(value):
    frames = template_frames("rows", 1, DEFAULT_SETTINGS)
    frames[0]["x"] = value
    with pytest.raises(ValueError):
        validate_frames(frames, 1)


def test_reject_overlap_and_invalid_settings():
    with pytest.raises(ValueError, match="重な"):
        validate_frames([dict(x=0,y=0,width=1,height=1)] * 2, 2)
    for changed in (dict(width=100000), dict(margin=False), dict(dpi=1), dict(font_path=1)):
        with pytest.raises(ValueError):
            validate_settings(dict(DEFAULT_SETTINGS, **changed))


def test_page_storage_keeps_history_and_existing_data(composition):
    store, data, images, latest = composition
    candidate = upload(images, latest, "red")
    before = {path: path.read_bytes() for path in store.root.parent.rglob("*") if path.is_file()}
    config = quiet_page(data, latest)
    store.save_page(1, config, story_version_id=latest["id"], expected_revision=0)
    changed = copy.deepcopy(config)
    changed["template"] = "rows"
    store.save_page(1, changed, story_version_id=latest["id"], expected_revision=1)
    store.save_settings(dict(DEFAULT_SETTINGS, width=600), expected_revision=2)
    fresh = PageStore(store.root.parent, data["project_id"]).load()
    assert fresh["revision"] == 3
    assert fresh["pages"]["1"]["template"] == "rows"
    assert fresh["history"][1]["before"] == config
    assert fresh["history"][2]["before"] == DEFAULT_SETTINGS
    for path, content in before.items():
        assert path.read_bytes() == content
    assert images.image_bytes(candidate)
    assert PageStore(store.root.parent, str(uuid4())).load()["pages"] == {}
    with pytest.raises(StorageError, match="更新"):
        store.save_settings(DEFAULT_SETTINGS, expected_revision=1)


def test_stale_story_rejected_and_removed_panels_keep_saved_layout(composition):
    store, data, _, latest = composition
    config = quiet_page(data, latest)
    store.save_page(1, config, story_version_id=latest["id"], expected_revision=0)
    changed_story = copy.deepcopy(latest["story"])
    changed_story["pages"][0]["panels"].pop()
    store.stories.save(changed_story, expected_version_id=latest["id"])
    with pytest.raises(StorageError, match="ストーリーが更新"):
        store.save_page(1, config, story_version_id=latest["id"], expected_revision=1)
    fresh = store.load()
    fallback = effective_page(fresh["pages"]["1"], data["project_id"], changed_story["pages"][0], str(uuid4()))
    assert len(fallback["bubbles"]) == 4
    assert fresh["pages"]["1"] == config


@pytest.mark.parametrize("content", ['{bad', '{}', '{"schema_version": 999}'])
def test_corrupt_composition_not_overwritten(composition, content):
    store, _, _, _ = composition
    store.path.write_text(content)
    with pytest.raises(StorageError, match="形式が不正"):
        store.save_settings(DEFAULT_SETTINGS, expected_revision=0)
    assert store.path.read_text() == content


def test_page_validation(composition):
    _, data, _, latest = composition
    config = default_page(data["project_id"], latest["story"]["pages"][0], latest["id"])
    assert validate_page(config)
    config["bubbles"].append(copy.deepcopy(config["bubbles"][0]))
    with pytest.raises(ValueError, match="重複"):
        validate_page(config)


@pytest.mark.parametrize("flow", ["horizontal", "vertical"])
def test_japanese_text_fits_without_clipping(font, flow):
    layer, chosen = draw_fitted_text((140, 160), "司書：「この本を届けよう。」\n月の図書館ー！", flow, font, 30, 8)
    bounds = layer.getbbox()
    assert 8 <= chosen <= 30
    assert bounds and 0 <= bounds[0] < bounds[2] <= 140 and 0 <= bounds[1] < bounds[3] <= 160
    with pytest.raises(TextLayoutError, match="収まりません"):
        draw_fitted_text((20, 20), "物語" * 200, flow, font, 20, 10)


def test_vertical_columns_start_at_right(font):
    image, _ = draw_fitted_text((100, 100), "月\n本", "vertical", font, 20, 20)
    assert image.crop((75, 0, 100, 24)).getbbox()
    assert image.crop((51, 0, 75, 24)).getbbox()
    assert image.crop((0, 0, 50, 100)).getbbox() is None


def test_font_validation_and_missing_glyph(font, monkeypatch):
    with pytest.raises(TextLayoutError, match="未対応"):
        check_glyphs("月\U0010ffff", font)
    with pytest.raises(TextLayoutError, match="読み込めません"):
        load_japanese_font("/missing/font.ttf")
    monkeypatch.setenv("MANGA_FONT_PATH", "/missing/font.ttf")
    with pytest.raises(TextLayoutError):
        find_japanese_font()
    latin = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if latin.exists():
        with pytest.raises(TextLayoutError, match="日本語を描画"):
            load_japanese_font(str(latin))


def test_render_rtl_color_and_source_unchanged(composition, font):
    store, data, images, latest = composition
    config = quiet_page(data, latest)
    originals = [upload(images, latest, color, panel=index) for index, color in enumerate(("red", "green", "blue"), 1)]
    before = [images.image_bytes(item) for item in originals]
    page = latest["story"]["pages"][0]
    rendered = render_page(page, latest["id"], images.load(), data, images, font)
    assert rendered.errors == rendered.warnings == ()
    with Image.open(BytesIO(rendered.png)) as image:
        assert image.size == (600, 900)
        for frame, color in zip(pixel_frames(page, config, data["settings"]), ((255,0,0), (0,128,0), (0,0,255))):
            assert image.getpixel((frame["x"] + frame["width"] // 2, frame["y"] + frame["height"] // 2)) == color
        assert image.getpixel((0, 0)) == (255,255,255)
    assert before == [images.image_bytes(item) for item in originals]
    assert not store.path.exists()


def test_image_contain_preserves_both_edges_and_alpha(composition, font):
    _, data, images, latest = composition
    config = quiet_page(data, latest)
    source = Image.new("RGBA", (300, 50), (0,0,0,0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0,0,49,49), fill="red")
    draw.rectangle((250,0,299,49), fill="blue")
    out = BytesIO()
    source.save(out, "PNG")
    upload(images, latest, None, content=out.getvalue())
    page = latest["story"]["pages"][0]
    rendered = render_page(page, latest["id"], images.load(), data, images, font)
    frame = pixel_frames(page, config, data["settings"])[0]
    with Image.open(BytesIO(rendered.png)) as image:
        y = frame["y"] + frame["height"] // 2
        assert image.getpixel((frame["x"] + 10, y)) == (255,0,0)
        assert image.getpixel((frame["x"] + frame["width"] - 10, y)) == (0,0,255)
        assert image.getpixel((frame["x"] + frame["width"] // 2, y)) == (255,255,255)


def test_missing_and_stale_images_are_explicit_placeholders(composition, font):
    _, data, images, latest = composition
    quiet_page(data, latest)
    candidate = upload(images, latest, "red")
    artwork = images.load()
    artwork["panels"]["1:1"]["candidates"][0]["source_panel"]["description"] = "以前の描写"
    # ファイル欠損を再現するが、実ファイルは消さない。
    artwork["panels"]["1:1"]["candidates"][0]["path"] = "images/panels/missing.png"
    result = render_page(latest["story"]["pages"][0], latest["id"], artwork, data, images, font)
    assert not result.errors
    assert any("プレースホルダー" in note for note in result.warnings)
    assert any("変更前" in note for note in result.warnings)
    assert images.image_bytes(candidate)


def test_cache_renders_only_changed_panel_and_has_memory_limit(composition, font):
    _, data, images, latest = composition
    config = quiet_page(data, latest)
    cache = RenderCache()
    page = latest["story"]["pages"][0]
    render_page(page, latest["id"], images.load(), data, images, font, cache)
    assert (cache.hits, cache.misses) == (0,3)
    render_page(page, latest["id"], images.load(), data, images, font, cache)
    assert (cache.hits, cache.misses) == (3,3)
    config["bubbles"] = default_page(data["project_id"], page, latest["id"])["bubbles"][:1]
    render_page(page, latest["id"], images.load(), data, images, font, cache)
    assert (cache.hits, cache.misses) == (5,4)
    small = RenderCache(max_bytes=10)
    small.put("a", (b"123456", (), ()))
    small.put("b", (b"789012", (), ()))
    assert small.bytes == 6 and list(small.entries) == ["b"]
    small.put("c", (b"x" * 11, (), ()))
    assert small.bytes == 6


def test_text_errors_block_all_exports(composition, font):
    _, data, images, latest = composition
    page = latest["story"]["pages"][0]
    config = default_page(data["project_id"], page, latest["id"])
    config["bubbles"][0]["text"] = "物語" * 2000
    data["pages"]["1"] = config
    rendered = render_page(page, latest["id"], images.load(), data, images, font)
    assert rendered.errors
    with pytest.raises(ValueError, match="出力できません"):
        page_png(rendered)
    with pytest.raises(ValueError, match="出力できません"):
        export_book(latest["story"], latest["id"], images.load(), data, images, font)
    with pytest.raises(ValueError, match="出力できません"):
        export_vertical([page], latest["id"], images.load(), data, images, font, width=600)


def test_pdf_png_zip_and_sns_export_real_content(composition, font):
    _, data, images, latest = composition
    for number in range(1,9):
        quiet_page(data, latest, number, template="hero_top")
    upload(images, latest, "red")
    art = images.load()
    cache = RenderCache()
    pdf, warnings = export_book(latest["story"], latest["id"], art, data, images, font, cache=cache)
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 8
    assert float(reader.pages[0].mediabox.width) == pytest.approx(288)
    assert float(reader.pages[0].mediabox.height) == pytest.approx(432)
    assert warnings
    first_image = next(iter(reader.pages[0].images)).image
    assert first_image.mode == "RGB" and first_image.size == (600,900)
    center = pixel_frames(latest["story"]["pages"][0], data["pages"]["1"], data["settings"])[0]
    pixel = first_image.getpixel((center["x"] + center["width"] // 2, center["y"] + center["height"] // 2))
    assert pixel[0] > 240 and pixel[1] < 15 and pixel[2] < 15
    pngs, _ = export_book(latest["story"], latest["id"], art, data, images, font, "png_zip", cache)
    with ZipFile(BytesIO(pngs)) as archive:
        assert archive.namelist() == [f"page_{number:03d}.png" for number in range(1,9)]
        with Image.open(BytesIO(archive.read("page_001.png"))) as image:
            assert image.size == (600,900)
            assert image.info["dpi"][0] == pytest.approx(150, abs=.1)
    sns, _ = export_vertical(latest["story"]["pages"], latest["id"], art, data, images, font, 600, cache)
    with ZipFile(BytesIO(sns)) as archive:
        order = json.loads(archive.read("reading_order.json"))
        assert [(item["page"],item["panel"]) for item in order] == [(page,panel) for page in range(1,9) for panel in range(1,4)]
        assert len({item["file"] for item in order}) > 1
        for item in order:
            with Image.open(BytesIO(archive.read(item["file"]))) as image:
                assert image.width == 600 and image.height <= 8000
                assert item["y"] + item["height"] <= image.height


def test_large_export_rejected_before_rendering(composition, font, monkeypatch):
    _, data, images, latest = composition
    data["settings"]["width"] = 2000
    latest["story"]["pages"] = latest["story"]["pages"] * 2
    monkeypatch.setattr("manga.page_exports.render_page", lambda *args: pytest.fail("事前検証より先に描画された"))
    with pytest.raises(ValueError, match="6000万"):
        export_book(latest["story"], latest["id"], images.load(), data, images, font)
