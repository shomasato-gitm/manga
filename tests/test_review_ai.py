import copy
import json
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from PIL import Image

from manga.review_context import load_context
from manga.review_prompts import new_request, review_prompt, response_example, parse_ai_review
from manga.review_storage import ReviewStore
from manga.review_assets import review_package
from manga.review_status import summary, task_current
from manga.storage import StorageError
from review_fixtures import create_review_example


@pytest.fixture
def review(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a,**k:pytest.fail('外部に送信した'))
    project, latest, art, pages = create_review_example(tmp_path, page_count=2)
    context = load_context(tmp_path, project.id)
    reference = art.load()['characters'][0]['references'][0]['id']
    request = new_request(context, [dict(page=1,panel=1), dict(page=1,panel=2)], [reference], '服装の連続性と感情', include_pages=True)
    request['prompt'] = review_prompt(project, context, request)
    store = ReviewStore(tmp_path, project.id)
    store.save_request(request, expected_revision=0)
    return tmp_path, project, latest, art, pages, context, request, store


def test_local_materials_include_only_selected_pages_panels_refs_and_response_format(review):
    root, project, latest, art, pages, context, request, store = review
    protected = {p:p.read_bytes() for p in art.root.rglob('*') if p.is_file() and p.name != 'review.json'}
    content, notes = review_package(context, request, request['prompt'])
    with ZipFile(BytesIO(content)) as archive:
        assert 'page_001.png' in archive.namelist() and 'page_002.png' not in archive.namelist()
        assert 'panel_comparison.png' in archive.namelist()
        assert json.loads(archive.read('response_format.json'))['request_id'] == request['id']
        with Image.open(BytesIO(archive.read('panel_comparison.png'))) as image: assert image.size == (640,360)
        assert len([n for n in archive.namelist() if n.startswith('references/')]) == 1
        prompt = archive.read('review_prompt.txt').decode()
        assert request['id'] in prompt and latest['id'] in prompt and '不明' in prompt
        assert '主観的' in prompt and '完成漫画の画像' in prompt and '服装の連続性と感情' in prompt
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['request']['targets'] == request['targets']
    assert all(p.read_bytes() == raw for p,raw in protected.items())


def test_ai_import_starts_unconfirmed_no_canonical_changes_and_explicit_tasks_and_ignore(review):
    root, project, latest, art, pages, context, request, store = review
    originals = (art.path.read_bytes(), pages.path.read_bytes(), pages.stories.path.read_bytes())
    payload = response_example(request)
    raw = '返答です\n```json\n' + json.dumps(payload, ensure_ascii=False) + '\n```'
    store.import_ai(raw, expected_revision=1, snapshot=context.snapshot)
    data = store.load(); result = data['ai_reviews'][0]
    assert not result['reviewed'] and set(result['decisions'].values()) == {'未確認'} and not data['tasks']
    assert summary(context, data)['unconfirmed_ai'] == 1
    store.create_task(result['issues'][0], origin='ai', source_id=result['id'], expected_revision=2, snapshot=context.snapshot)
    assert store.load()['ai_reviews'][0]['decisions'][result['issues'][0]['id']] == 'タスク化'
    store.decide_ai(result['id'], expected_revision=3, snapshot=context.snapshot)
    assert summary(context, store.load())['unconfirmed_ai'] == 0
    assert originals == (art.path.read_bytes(), pages.path.read_bytes(), pages.stories.path.read_bytes())
    with pytest.raises(ValueError, match='取り込み済み'): store.import_ai(raw, expected_revision=4, snapshot=context.snapshot)


@pytest.mark.parametrize('fault', ['syntax', 'duplicate_key', 'missing', 'page', 'panel', 'request', 'story', 'severity', 'target', 'bool', 'duplicate_issue', 'nonfinite'])
def test_invalid_ai_json_is_rejected_with_existing_review_unchanged(review, fault):
    root, project, latest, art, pages, context, request, store = review
    data = response_example(request)
    if fault == 'syntax': raw = '{"bad":}'
    elif fault == 'duplicate_key': raw = '{"schema_version":1,"schema_version":1}'
    elif fault == 'nonfinite': raw = '{"schema_version":NaN}'
    else:
        if fault == 'missing': data['issues'][0].pop('reason')
        if fault == 'page': data['issues'][0]['page'] = 2
        if fault == 'panel': data['issues'][0]['panel'] = 3
        if fault == 'request': data['request_id'] = str(uuid4())
        if fault == 'story': data['story_version_id'] = str(uuid4())
        if fault == 'severity': data['issues'][0]['severity'] = '最高'
        if fault == 'target': data['issues'][0]['target'] = 'image_api'
        if fault == 'bool': data['issues'][0]['needs_human_check'] = 'true'
        if fault == 'duplicate_issue': data['issues'].append(copy.deepcopy(data['issues'][0]))
        raw = json.dumps(data)
    before = store.path.read_bytes()
    with pytest.raises(ValueError): store.import_ai(raw, expected_revision=1, snapshot=context.snapshot)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('change', ['story', 'direction', 'image', 'composition'])
def test_old_review_rejected_after_any_relevant_production_change(review, change):
    root, project, latest, art, pages, context, request, store = review
    if change == 'story':
        story = copy.deepcopy(latest['story']); story['pages'][0]['panels'][0]['description'] += '夜。'
        pages.stories.save(story, expected_version_id=latest['id'])
    if change == 'direction': art.save_direction(1,1,dict(location='廊下'), story_version_id=latest['id'], expected_revision=art.load()['revision'])
    if change == 'image':
        image = art.load()['panels']['1:1']['candidates'][0]
        path = art.image_path(image); path.write_bytes(path.read_bytes()+b'changed signature')
    if change == 'composition':
        data = pages.load(); config = data['pages']['1']; config['image_placements']['1']['zoom'] = 1.5
        pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    before = store.path.read_bytes()
    fresh = load_context(root, project.id)
    with pytest.raises(ValueError, match='古い'): store.import_ai(json.dumps(response_example(request)), expected_revision=1, snapshot=fresh.snapshot)
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError, match='変わり'): review_package(fresh, request, request['prompt'])


def test_old_ai_task_retains_discovery_fingerprint_and_ignored_subjective_issue_does_not_block(review):
    root, project, latest, art, pages, context, request, store = review
    store.import_ai(json.dumps(response_example(request)), expected_revision=1, snapshot=context.snapshot)
    result = store.load()['ai_reviews'][0]
    data = pages.load(); pages.save_settings(dict(data['settings'], dpi=72), expected_revision=data['revision'])
    fresh = load_context(root, project.id)
    store.create_task(result['issues'][0], origin='ai', source_id=result['id'], expected_revision=2, snapshot=fresh.snapshot)
    task = store.load()['tasks'][0]
    assert task['snapshot'] == context.snapshot and not task_current(task, fresh.snapshot)
    store.decide_ai(result['id'], result['issues'][0]['id'], expected_revision=3, snapshot=fresh.snapshot)
    assert store.load()['ai_reviews'][0]['decisions'][result['issues'][0]['id']] == '対応不要'


def test_zero_ai_issues_still_requires_human_review(review):
    root, project, latest, art, pages, context, request, store = review
    data = response_example(request); data['issues'] = []
    store.import_ai(json.dumps(data), expected_revision=1, snapshot=context.snapshot)
    state = summary(context, store.load())
    assert state['unconfirmed_ai'] == 1 and state['manual_needed'] == 2 and state['finals'] == 0


def test_request_validation_duplicate_targets_refs_and_unrequested_scope(review):
    root, project, latest, art, pages, context, request, store = review
    reference = request['reference_ids'][0]
    for targets, references in [([dict(page=1,panel=1)]*2, []), ([dict(page=1,panel=1)], [reference]*2), ([dict(page=1,panel=1)], [str(uuid4())]), ([dict(page=100,panel=1)], [])]:
        with pytest.raises(ValueError): new_request(context, targets, references)
    payload = response_example(request)
    raw = json.dumps(payload)*20_000
    with pytest.raises(ValueError, match='1MB'): parse_ai_review(raw, [request], context)
