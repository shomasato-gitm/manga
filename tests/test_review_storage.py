import copy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from manga.review_context import load_context
from manga.review_checks import scan, make_issue
from manga.review_storage import ReviewStore
from manga.review_status import page_status, summary, task_status, export_warnings
from manga.review_models import MANUAL_ITEMS
from manga.storage import StorageError
from manga.studio_storage import StudioStore
from manga.studio_assets import production_fingerprint
from review_fixtures import create_review_example


@pytest.fixture
def setup(tmp_path):
    project, latest, art, pages = create_review_example(tmp_path, page_count=2)
    return tmp_path, project, latest, art, pages, ReviewStore(tmp_path, project.id)


def snapshot(setup): return load_context(setup[0], setup[1].id).snapshot


def save_scan(setup):
    root, project, *_, store = setup
    context = load_context(root, project.id)
    result = scan(context)
    store.save_scan(result, expected_revision=store.load()['revision'])
    return context, result


def test_manual_defaults_save_reload_reconfirm_and_per_page_invalidation(setup):
    root, project, latest, art, pages, store = setup
    context, result = save_scan(setup)
    assert not page_status(store.load(), context.snapshot, 1)['manual_done']
    answers = {k:'問題なし' for k in MANUAL_ITEMS}
    store.save_manual(1, answers, '顔・服装を比較した', expected_revision=1, snapshot=context.snapshot)
    assert ReviewStore(root, project.id).load()['manual']['1']['answers'] == answers
    assert page_status(store.load(), context.snapshot, 1)['manual_done']
    data = pages.load(); config = data['pages']['2']; config['image_placements']['1']['zoom'] = 1.2
    pages.save_page(2, config, story_version_id=latest['id'], expected_revision=data['revision'])
    changed = snapshot(setup)
    assert page_status(store.load(), changed, 1)['manual_done']
    data = pages.load(); config = data['pages']['1']; config['image_placements']['1']['zoom'] = 1.2
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    changed = snapshot(setup); state = page_status(store.load(), changed, 1)
    assert state['manual_stale'] and not state['manual_done']
    assert store.load()['manual']['1']['answers'] == answers
    store.save_manual(1, answers, '変更後も確認した', expected_revision=2, snapshot=changed)
    assert page_status(store.load(), changed, 1)['manual_done']
    assert store.load()['events'][-1]['before']['notes'] == '顔・服装を比較した'


def test_final_requires_explicit_preview_and_manual_and_is_invalidated_by_review_change(setup):
    root, project, latest, art, pages, store = setup
    context, result = save_scan(setup)
    with pytest.raises(ValueError): store.finalize(1, expected_revision=1, snapshot=context.snapshot)
    store.save_manual(1, {k:'問題なし' for k in MANUAL_ITEMS}, '', expected_revision=1, snapshot=context.snapshot)
    with pytest.raises(ValueError): store.finalize(1, expected_revision=2, snapshot=context.snapshot)
    store.confirm_preview(1, expected_revision=2, snapshot=context.snapshot)
    store.finalize(1, expected_revision=3, snapshot=context.snapshot)
    assert summary(context, store.load())['finals'] == 1
    store.save_manual(1, {k:'要修正' for k in MANUAL_ITEMS}, '見落とし', expected_revision=4, snapshot=context.snapshot)
    assert not page_status(store.load(), context.snapshot, 1)['final']
    assert any(e['kind'] == 'final_invalidated' for e in store.load()['events'])


def test_fix_recheck_and_complete_require_human_confirmation_and_preserve_original(setup):
    root, project, latest, art, pages, store = setup
    snap = snapshot(setup)
    issue = make_issue(project.id, '目視', '美咲の顔が基準と違う', page=1, panel=2)
    store.create_task(issue, origin='manual', expected_revision=0, snapshot=snap)
    task = store.load()['tasks'][0]
    with pytest.raises(ValueError): store.update_task(task['id'], '確認完了', '未修正', expected_revision=1, snapshot=snap, rechecked=True)
    store.update_task(task['id'], '対応中', '画像を見直す', expected_revision=1, snapshot=snap)
    art.save_direction(1,2,dict(location='窓際',character_ids=[]), story_version_id=latest['id'], expected_revision=art.load()['revision'])
    snap2 = snapshot(setup)
    store.update_task(task['id'], '再確認待ち', '画像を再採用した', expected_revision=2, snapshot=snap2)
    with pytest.raises(ValueError): store.update_task(task['id'], '確認完了', '確認していない', expected_revision=3, snapshot=snap2)
    store.update_task(task['id'], '確認完了', '現在の画像を確認した', expected_revision=3, snapshot=snap2, rechecked=True)
    assert task_status(store.load()['tasks'][0], snap2) == '確認完了'
    assert store.load()['tasks'][0]['snapshot'] == snap
    data = pages.load(); config = data['pages']['1']; config['image_placements']['1']['zoom'] = 1.1
    pages.save_page(1, config, story_version_id=latest['id'], expected_revision=data['revision'])
    assert task_status(store.load()['tasks'][0], snapshot(setup)) == '再確認待ち'
    assert len(store.load()['events']) == 3


def test_duplicate_tasks_revision_and_source_conflicts_and_write_failure_protect_bytes(setup):
    root, project, latest, art, pages, store = setup
    snap = snapshot(setup); issue = make_issue(project.id, '目視', '顔を確認', page=1, panel=1)
    store.create_task(issue, origin='manual', expected_revision=0, snapshot=snap)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='未解決タスク'): store.create_task(issue, origin='manual', expected_revision=1, snapshot=snap)
    with pytest.raises(StorageError, match='別画面'): store.save_manual(1, {k:'問題なし' for k in MANUAL_ITEMS}, '', expected_revision=0, snapshot=snap)
    with patch('manga.review_storage.write_json_atomic', side_effect=OSError('disk full')):
        with pytest.raises(StorageError, match='空き容量'): store.update_task(store.load()['tasks'][0]['id'], '対応中', '', expected_revision=1, snapshot=snap)
    assert store.path.read_bytes() == before
    data = pages.load(); pages.save_settings(dict(data['settings'], dpi=72), expected_revision=data['revision'])
    with pytest.raises(StorageError, match='制作データが変更'): store.save_manual(1, {k:'問題なし' for k in MANUAL_ITEMS}, '', expected_revision=1, snapshot=snap)
    assert store.path.read_bytes() == before


def test_simultaneous_writers_only_one_succeeds(setup):
    root, project, latest, art, pages, store = setup
    snap = snapshot(setup)
    def writer(page):
        try:
            store.save_manual(page, {k:'問題なし' for k in MANUAL_ITEMS}, '', expected_revision=0, snapshot=snap)
            return True
        except StorageError: return False
    with ThreadPoolExecutor(max_workers=2) as executor: outcomes = list(executor.map(writer, (1,2)))
    assert outcomes.count(True) == outcomes.count(False) == 1
    assert store.load()['revision'] == 1


def test_quality_mutations_preserve_all_existing_files_and_studio_export_records(setup):
    root, project, latest, art, pages, store = setup
    studio = StudioStore(root, project.id)
    fp = production_fingerprint(latest, art.load(), pages.load(), art.root)
    studio.record_export(dict(fingerprint=fp,pages=[1,2],kind='pdf',filename='existing.pdf'))
    # 模した既存の他工程データも品質機能の保存対象外である。
    for name in ('editorial.json', 'assistant.json'):
        (art.root/name).write_text('{"existing_history":"preserve"}')
    protected = {p:p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.startswith('.')}
    context, result = save_scan(setup)
    store.save_manual(1, {k:'対象外' for k in MANUAL_ITEMS}, '', expected_revision=1, snapshot=context.snapshot)
    store.confirm_preview(1, expected_revision=2, snapshot=context.snapshot)
    assert all(p.read_bytes() == content for p,content in protected.items())
    assert studio.load()['exports'][0]['filename'] == 'existing.pdf'
    # 成果物の記録だけ増えても制作内容の検査は古くならない。
    studio.record_export(dict(fingerprint=fp,pages=[1],kind='page',filename='new.png'))
    assert snapshot(setup) == context.snapshot


def test_unconfirmed_subjective_issues_only_warn_and_do_not_block_exports(setup):
    from manga.page_exports import export_book
    from manga.japanese_text import find_japanese_font
    root, project, latest, art, pages, store = setup
    context = load_context(root, project.id)
    notes = export_warnings(context, store.load())
    assert any('未確認' in note for note in notes)
    content, warnings = export_book(latest['story'], latest['id'], art.load(), pages.load(), art, find_japanese_font(), 'pdf')
    assert content.startswith(b'%PDF')


@pytest.mark.parametrize('damage', ['missing', 'state', 'id'])
def test_corrupt_review_never_overwritten(setup, damage):
    import json
    root, project, latest, art, pages, store = setup
    store.save_manual(1, {k:'未確認' for k in MANUAL_ITEMS}, '', expected_revision=0, snapshot=snapshot(setup))
    data = store.load()
    if damage == 'missing': data.pop('manual')
    if damage == 'state': data['manual']['1']['answers']['emotion'] = '合格'
    if damage == 'id': data['events'].append(copy.deepcopy(data['events'][0]))
    store.path.write_text(json.dumps(data)); before = store.path.read_bytes()
    with pytest.raises(StorageError): store.load()
    with pytest.raises(StorageError): store.save_manual(1, {k:'問題なし' for k in MANUAL_ITEMS}, '', expected_revision=1, snapshot=snapshot(setup))
    assert store.path.read_bytes() == before


def test_auto_issues_create_tasks_with_original_scan_evidence_and_no_duplicate(setup):
    root, project, latest, art, pages, store = setup
    # 古い版を残したまま新しい脚本にすると、対応対象になる自動指摘を作れる。
    story = copy.deepcopy(latest['story']); story['pages'][0]['panels'][0]['description'] += '時間が進む。'
    pages.stories.save(story, expected_version_id=latest['id'])
    context, result = save_scan(setup)
    issue = next(i for i in result['issues'] if i['category'] == '古い作画指示')
    store.create_task(issue, origin='auto', source_id=result['id'], expected_revision=1, snapshot=context.snapshot)
    task = store.load()['tasks'][0]
    assert task['snapshot'] == result['snapshot'] and task['source_id'] == result['id']
    assert store.load()['scans'][0] == result
    with pytest.raises(ValueError, match='未解決タスク'):
        store.create_task(issue, origin='auto', source_id=result['id'], expected_revision=2, snapshot=context.snapshot)


def test_original_image_file_change_invalidates_saved_manual_answers(setup):
    root, project, latest, art, pages, store = setup
    context, _ = save_scan(setup)
    store.save_manual(1, {k:'問題なし' for k in MANUAL_ITEMS}, '旧画像を確認', expected_revision=1, snapshot=context.snapshot)
    image = art.load()['panels']['1:1']['candidates'][0]; path = art.image_path(image)
    # テストフォルダ内の保存済み画像の変更だけで検出でき、JSONの版に依存しない。
    path.write_bytes(path.read_bytes()+b'new-file-signature')
    fresh = load_context(root, project.id)
    assert page_status(store.load(), fresh.snapshot, 1)['manual_stale']
    assert not page_status(store.load(), fresh.snapshot, 2)['manual_stale']
