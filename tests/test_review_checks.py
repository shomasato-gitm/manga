import copy
import json
from uuid import uuid4
from unittest.mock import patch
from io import BytesIO

import pytest
from PIL import Image

from manga.review_context import load_context
from manga.review_checks import scan
from manga.review_assets import save_thumbnails, read_thumbnail, page_preview
from manga.review_storage import ReviewStore
from manga.review_status import summary
from manga.page_render import adopted_image
from manga.page_images import image_scale, placement_for
from manga.page_layout import pixel_frames
from manga.storage import write_json_atomic
from review_fixtures import create_review_example, sample_image


@pytest.fixture
def example(tmp_path):
    return (tmp_path, *create_review_example(tmp_path))


def run(example, **options):
    root, project, *_ = example
    return scan(load_context(root, project.id), **options)


def test_uninspected_is_not_clean_and_clean_8_page_scan_is_not_manual_review(example):
    root, project, latest, art, pages = example
    store = ReviewStore(root, project.id)
    context = load_context(root, project.id)
    before = summary(context, store.load())
    assert before['pages'] == 8 and before['panels'] == before['adopted'] == 24
    assert before['issues'] is None and before['manual_needed'] == 8 and before['previews'] == before['finals'] == 0
    progress = []
    result = run(example, progress=lambda d,t:progress.append((d,t)))
    assert result['completed'] and result['rendered_pages'] == list(range(1,9))
    assert progress == [(i,8) for i in range(1,9)]
    assert not [i for i in result['issues'] if i['blocks_export']], result['issues']
    assert not any(i['category'] == '未分割セリフ' for i in result['issues']), '分割済みの話者ラベルを除いた本文を誤検出しない'
    store.save_scan(result, expected_revision=0)
    after = summary(context, store.load())
    assert after['manual_needed'] == 8 and after['finals'] == 0 and after['issues'] is not None


def test_missing_unadopted_broken_and_small_images_detected_at_correct_panel(example):
    root, project, latest, art, _ = example
    data = art.load()
    data['panels']['1:1']['selected_image_id'] = None
    write_json_atomic(art.path, data)
    missing = adopted_image(data, 1, 2); art.image_path(missing).rename(art.image_path(missing).with_suffix('.saved'))
    broken = adopted_image(data, 1, 3); art.image_path(broken).write_bytes(b'not an image')
    tiny = adopted_image(data, 2, 1); art.image_path(tiny).write_bytes(sample_image((64,64)))
    result = run(example)
    assert any(i['category'] == '画像未採用' and (i['page'],i['panel']) == (1,1) for i in result['issues'])
    assert any(i['category'] == '画像ファイル欠損' and (i['page'],i['panel']) == (1,2) for i in result['issues'])
    assert any(i['category'] == '画像読込不良' and (i['page'],i['panel']) == (1,3) for i in result['issues'])
    assert any(i['category'] == '解像度不足' and (i['page'],i['panel']) == (2,1) for i in result['issues'])


def test_stale_directions_images_and_composition_are_detected(example):
    root, project, latest, art, pages = example
    story = copy.deepcopy(latest['story']); story['pages'][0]['panels'][1]['description'] += '夜に変わる。'
    pages.stories.save(story, expected_version_id=latest['id'])
    categories = {i['category'] for i in run(example)['issues']}
    assert {'古い作画指示', '古い採用画像', '古いページ編集'} <= categories


def test_character_deletion_and_nonexistent_id_do_not_pass(example):
    root, project, latest, art, pages = example
    data = art.load(); data['characters'][0]['deleted_at'] = data['characters'][0]['updated_at']
    write_json_atomic(art.path, data)
    assert any(i['category'] == '人物ID不正' for i in run(example)['issues'])
    data['panels']['1:1']['direction']['character_ids'] = [str(uuid4())]
    write_json_atomic(art.path, data)
    result = run(example)
    assert not result['completed'] and any(i['category'] == 'データ読込エラー' for i in result['issues'])


def test_text_overflow_unsupported_glyph_and_unsplit_speakers_reuse_rendering(example):
    root, project, latest, art, pages = example
    data = pages.load(); config = data['pages']['1']
    speech = next(b for b in config['bubbles'] if b['panel'] == 2)
    speech.update(text='あ'*1000, box=dict(x=.1,y=.1,width=.1,height=.1))
    config['bubbles'][0]['text'] = '未対応文字\U0001f9d9'
    other = next(b for b in config['bubbles'] if b['panel'] == 3)
    other['text'] = '悠真：「またね」\n美咲：「約束ね」'
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    result = run(example)
    errors = [i for i in result['issues'] if i['category'] == '描画エラー']
    assert any('収まりません' in i['message'] and i['blocks_export'] for i in errors)
    assert any('未対応の文字' in i['message'] for i in errors)
    assert any(i['category'] == '未分割セリフ' and i['panel'] == 3 for i in result['issues'])
    assert 1 not in result['rendered_pages']


@pytest.mark.parametrize('fault', ['overlap', 'outside', 'bubble'])
def test_invalid_saved_layout_is_reported_without_overwriting(example, fault):
    root, project, latest, art, pages = example
    data = pages.load(); config = data['pages']['1']
    if fault == 'bubble': config['bubbles'][0]['box']['x'] = 1.2
    else:
        config['frames'] = [dict(x=0,y=0,width=.5,height=.3)]*3
        if fault == 'outside': config['frames'][0] = dict(x=.9,y=0,width=.5,height=.3)
    write_json_atomic(pages.path, data); before = pages.path.read_bytes()
    result = run(example)
    assert any(i['category'] == 'データ読込エラー' and i['blocks_export'] for i in result['issues'])
    assert not result['completed'] and pages.path.read_bytes() == before


def test_resolution_uses_display_crop_zoom_and_sns_pixels_not_fixed_threshold(example):
    root, project, latest, art, pages = example
    def deficits(): return {(i['page'],i['panel']) for i in run(example)['issues'] if i['category'] == '解像度不足'}
    assert (1,1) not in deficits()
    data = pages.load(); config = data['pages']['1']; config['image_placements']['1']['zoom'] = 4
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    assert (1,1) in deficits()
    data = pages.load(); config['image_placements']['1'].update(zoom=1, mode='contain')
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    assert (1,1) not in deficits()
    sns = run(example, sns_width=1080)
    assert any(i['category'] == '解像度不足' and 'SNS横幅1080' in i['message'] for i in sns['issues'])
    assert image_scale((900,1000),(540,420),dict(mode='cover',zoom=1)) == .6


def test_thumbnails_are_small_persisted_and_reused_without_original_reads(example):
    root, project, latest, art, pages = example
    context = load_context(root, project.id); progress = []
    assert read_thumbnail(context, 1) is None
    assert save_thumbnails(context, progress=lambda d,t:progress.append((d,t))) == []
    assert progress[-1] == (8,8)
    with patch('manga.review_assets.original_bytes', side_effect=AssertionError('元画像を読んだ')):
        assert save_thumbnails(context) == []
        for n in range(1,9):
            with Image.open(BytesIO(read_thumbnail(context, n))) as thumb: assert thumb.size == (180,270)
    page = page_preview(context, 1)
    assert (page.width, page.height) == (600,900)
    data = pages.load(); config = data['pages']['1']; config['image_placements']['1']['zoom'] = 1.5
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    fresh = load_context(root, project.id)
    assert read_thumbnail(fresh, 1) is None and read_thumbnail(fresh, 2) is not None


def test_missing_story_and_empty_visible_speech_remain_explicit_issues(tmp_path):
    from manga.models import Project
    from manga.storage import ProjectStore
    project = Project(title='未作成の漫画', genre='青春', synopsis='確認前')
    ProjectStore(tmp_path).save(project)
    result = scan(load_context(tmp_path, project.id))
    assert not result['completed'] and any(i['category'] == 'ストーリー未保存' for i in result['issues'])
    root = tmp_path/'separate'; root.mkdir()
    project, latest, art, pages = create_review_example(root, page_count=1)
    data = pages.load(); config = data['pages']['1']
    speech = next(b for b in config['bubbles'] if b['source'] == 'dialogue')
    speech['text'] = ''
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    issues = scan(load_context(root, project.id))['issues']
    assert any(i['category'] == '空のセリフ' for i in issues)


def test_output_plan_budget_error_reuses_export_validation(example, monkeypatch):
    from manga import page_exports
    monkeypatch.setattr(page_exports, 'MAX_EXPORT_PIXELS', 100)
    result = run(example, sns_width=600)
    assert any(i['category'] == '全ページ出力計画' and '6000万画素' in i['message'] for i in result['issues'])


def test_inline_ambiguous_speaker_text_is_kept_and_sent_to_manual_review(example):
    root, project, latest, art, pages = example
    data = pages.load(); config = data['pages']['1']
    speech = next(b for b in config['bubbles'] if b['panel'] == 3)
    speech['text'] = '悠真：「またね」 美咲：「約束ね」'
    speech.pop('needs_review', None)
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    before = pages.path.read_bytes()
    result = run(example)
    assert any(i['category'] == '未分割セリフ' and i['page'] == 1 and i['panel'] == 3 for i in result['issues'])
    assert pages.path.read_bytes() == before
