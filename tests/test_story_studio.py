import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from manga.storage import ProjectStore, StorageError
from manga.story_studio import StudioService
from manga.story_studio_samples import sample_studio_response
from manga.story_concept_storage import ConceptStore
from manga.story_studio_status import lineage, exact_evaluation, references_for_version, regression_notes, score_rows
from manga.editorial_prompts import analysis_template
from manga.editorial_models import MODES
from manga.story_techniques import technique_options


@pytest.fixture
def studio(tmp_path, manga_project):
    ProjectStore(tmp_path).save(manga_project)
    return StudioService(tmp_path, manga_project.id)


def request(service, kind, **kwargs):
    return service.create_request(kind, expected_revision=service.concepts.load()['revision'], **kwargs)


def answer(service, req, **kwargs):
    return service.import_response(sample_studio_response(req, **kwargs), expected_revision=service.concepts.load()['revision'])


def detail(service, choices=None):
    req = request(service, 'concepts', choices=choices)
    batch = answer(service, req)
    service.select(batch['id'], batch['payload']['concepts'][0]['id'], expected_revision=service.concepts.load()['revision'])
    req = request(service, 'detail')
    return answer(service, req), req


def add_reference(service):
    ed = service.editorial
    ref = ed.save_reference(dict(title='要約された参考作品', medium='映画', techniques='段階的な情報開示'), expected_revision=ed.load()['revision'])
    payload = analysis_template(ref['id'], ref['version'])
    payload['aspects']['opening'].update(status='ユーザー要約に基づく', abstract_technique='感情とともに情報を段階的に開く', basis='利用者の短い要約')
    payload['aspects']['ending'].update(status='未確認', abstract_technique='未確認なので選択不可')
    ed.save_analysis(ref['id'], json.dumps(payload), expected_revision=ed.load()['revision'])
    return ref


def test_three_concepts_selection_and_detail_trace_without_changing_story_format(studio):
    version, req = detail(studio)
    data = ConceptStore(studio.directory, studio.project_id).load()
    assert len(data['batches'][0]['payload']['concepts']) == 3
    assert data['details'][0]['selection_id'] == data['selected_id']
    assert data['details'][0]['request_id'] == req['id']
    assert data['details'][0]['version_id'] == version['id']
    assert set(version['story']) == {'schema_version', 'title', 'summary', 'pages'}
    assert version['story']['schema_version'] == 1
    assert len(version['story']['pages']) == 8
    assert sum(len(p['panels']) for p in version['story']['pages']) == 24
    assert studio.stories.load_history()[0]['id'] == version['id']


def test_current_analysis_and_selected_techniques_reach_all_four_prompts(studio):
    ref = add_reference(studio)
    options, _ = technique_options(studio.editorial.load(), [ref['id']])
    assert {t['id'] for t in options[0]['techniques']} == {'registered', 'opening'}
    version, req = detail(studio, {ref['id']: ['opening']})
    assert req['context']['references'][0]['analysis_id']
    assert req['context']['references'][0]['reference_version'] == 1
    assert len(req['context']['references'][0]['techniques']) == 1
    evaluation_request = request(studio, 'evaluation'); answer(studio, evaluation_request)
    improvement_request = request(studio, 'improvement')
    for prompt in [r['prompt'] for r in studio.concepts.load()['requests']]:
        assert '感情とともに情報を段階的に開く' in prompt
        assert '固有の場面' in prompt and '未確認' in prompt
    assert references_for_version(studio.concepts.load(), studio.stories.load_history(), version['id']) == req['context']['references']
    assert improvement_request['context']['references'] == req['context']['references']


def test_three_cycles_exact_evaluations_history_comparison_and_human_finalization(studio):
    initial, _ = detail(studio)
    for points in (6, 7, 8):
        answer(studio, request(studio, 'evaluation'), score=points)
        answer(studio, request(studio, 'improvement'))
        latest = studio.stories.load_history()[-1]
        assert exact_evaluation(studio.editorial.load(), latest) is None
    evaluation = answer(studio, request(studio, 'evaluation'), score=9)
    history, data = studio.stories.load_history(), studio.concepts.load()
    assert len(history) == 4 and history[0]['id'] == initial['id']
    assert lineage(data, history, history[-1]['id'])['cycles'] == 3
    assert len(data['improvements']) == 3
    assert all(r['changes'] and r['reason'] for r in data['improvements'])
    assert [r['総合点'] for r in score_rows(studio.editorial.load(), history)] == [60, 70, 80, 90]
    with pytest.raises(ValueError, match='最終確認'):studio.finalize(expected_version_id=studio.stories.load_history()[-1]['id'], expected_evaluation_id=studio.editorial.load()['evaluations'][-1]['id'], expected_revision=data['revision'])
    assert studio.editorial.load()['finalizations'] == []
    studio.finalize(expected_version_id=studio.stories.load_history()[-1]['id'], expected_evaluation_id=studio.editorial.load()['evaluations'][-1]['id'], expected_revision=data['revision'], reviewed=True)
    assert studio.editorial.load()['finalizations'][-1]['story_version_id'] == history[-1]['id']
    assert studio.concepts.load()['finalizations'][-1]['evaluation_id'] == evaluation['id']


@pytest.mark.parametrize('fault', ['two', 'duplicate_id', 'same_conflict', 'wrong_request', 'missing', 'nan'])
def test_invalid_concepts_do_not_change_confirmed_files(studio, fault):
    req = request(studio, 'concepts')
    payload = sample_studio_response(req)
    before = studio.concepts.path.read_bytes()
    if fault == 'two':payload['concepts'].pop()
    if fault == 'duplicate_id':payload['concepts'][1]['id'] = payload['concepts'][0]['id']
    if fault == 'same_conflict':payload['concepts'][1]['conflict'] = payload['concepts'][0]['conflict']
    if fault == 'wrong_request':payload['request_id'] = 'wrong'
    if fault == 'missing':payload['concepts'][0].pop('title')
    raw = json.dumps(payload) if fault != 'nan' else '{"schema_version":NaN}'
    with pytest.raises(ValueError):studio.import_response(raw, expected_revision=1)
    assert studio.concepts.path.read_bytes() == before and studio.stories.load_history() == []


def test_changed_analysis_or_selected_concept_rejects_stale_detail(studio):
    ref = add_reference(studio)
    _, detail_req = detail(studio, {ref['id']: ['opening']})
    ed = studio.editorial
    ed.save_reference(dict(ref, notes='登録内容変更'), reference_id=ref['id'], expected_revision=ed.load()['revision'])
    options, warnings = technique_options(ed.load(), [ref['id']])
    assert warnings and options[0]['analysis_id'] is None
    with pytest.raises(ValueError, match='古く'):request(studio, 'detail')
    assert len(studio.stories.load_history()) == 1


def test_old_evaluation_and_wrong_version_cannot_drive_improvement(studio):
    detail(studio)
    first = answer(studio, request(studio, 'evaluation'))
    pending = request(studio, 'improvement')
    second = answer(studio, request(studio, 'evaluation'), score=8)
    with pytest.raises(ValueError, match='古く'):
        answer(studio, pending)
    with pytest.raises(ValueError, match='最新評価'):
        request(studio, 'improvement', evaluation_id=first['id'])
    current = request(studio, 'improvement', evaluation_id=second['id'])
    answer(studio, current)
    with pytest.raises(ValueError, match='版が変わ'):
        answer(studio, pending)


def test_improvement_scope_limit_extension_and_decline_warning(studio):
    version, _ = detail(studio)
    studio.set_policy(85, 1, expected_revision=studio.concepts.load()['revision'])
    answer(studio, request(studio, 'evaluation'), score=9)
    options = dict(mode=MODES[1], criteria=['opening'], targets=[dict(page=1, panel=1)], reason='導入の動作だけ修正')
    req = request(studio, 'improvement', options=options)
    bad = sample_studio_response(req); bad['story']['pages'][1]['summary'] = '範囲外の変更'
    with pytest.raises(ValueError, match='範囲外'):
        studio.import_response(bad, expected_revision=studio.concepts.load()['revision'])
    answer(studio, req)
    answer(studio, request(studio, 'evaluation'), score=6)
    with pytest.raises(ValueError, match='回数'):request(studio, 'improvement')
    notes = regression_notes(studio.concepts.load(), studio.editorial.load(), studio.stories.load_history(), studio.stories.load_history()[-1])
    assert any('低下' in n for n in notes)
    with pytest.raises(ValueError, match='残る課題'):
        studio.finalize(expected_version_id=studio.stories.load_history()[-1]['id'], expected_evaluation_id=studio.editorial.load()['evaluations'][-1]['id'], expected_revision=studio.concepts.load()['revision'], reviewed=True)
    studio.extend(1, '指摘された導入をもう一度確認する', expected_version_id=studio.stories.load_history()[-1]['id'], expected_revision=studio.concepts.load()['revision'])
    answer(studio, request(studio, 'improvement'))
    assert len(studio.stories.load_history()) == 3


def test_restore_keeps_origin_and_requires_new_evaluation(studio):
    initial, req = detail(studio)
    answer(studio, request(studio, 'evaluation'))
    answer(studio, request(studio, 'improvement'))
    restored = studio.restore(initial['id'], expected_version_id=studio.stories.load_history()[-1]['id'], expected_revision=studio.concepts.load()['revision'])
    assert restored['id'] != initial['id']
    assert lineage(studio.concepts.load(), studio.stories.load_history(), restored['id'])['origin']['request_id'] == req['id']
    assert exact_evaluation(studio.editorial.load(), restored) is None


def test_partial_detail_save_resumes_without_duplicate_versions(studio):
    batch = answer(studio, request(studio, 'concepts'))
    studio.select(batch['id'], batch['payload']['concepts'][0]['id'], expected_revision=studio.concepts.load()['revision'])
    req = request(studio, 'detail')
    from manga.sync_storage import write_json_atomic
    def fail(path, data):
        if Path(path) == studio.concepts.path:raise StorageError('disk full')
        return write_json_atomic(path, data)
    with patch('manga.sync_storage.write_json_atomic', side_effect=fail):
        with pytest.raises(StorageError):answer(studio, req)
    assert studio.transactions.pending()
    with pytest.raises(ValueError, match='途中'):request(studio, 'concepts')
    studio.transactions.resume()
    assert len(studio.stories.load_history()) == 1
    assert studio.concepts.load()['details'][0]['version_id'] == studio.stories.load_history()[0]['id']
    assert list((studio.transactions.root/'github_sync'/'transactions').rglob('before/story_concepts.json'))


def test_concurrent_requests_conflict_instead_of_losing_history(studio):
    def create(_):
        try:return studio.create_request('concepts', expected_revision=0)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results = list(pool.map(create, range(2)))
    assert sum(r is not None for r in results) == 1
    assert len(studio.concepts.load()['requests']) == 1


def test_manual_edit_keeps_origin_but_unrelated_import_does_not(studio):
    original, _ = detail(studio)
    updated = copy.deepcopy(original['story']); updated['pages'][0]['summary'] = '手動でページを編集'
    edited = studio.stories.save(updated, expected_version_id=original['id'], action='ページ編集')
    data = studio.concepts.load()
    assert lineage(data, studio.stories.load_history(), edited['id'])['origin']
    imported = studio.stories.save(updated, expected_version_id=edited['id'])
    assert lineage(data, studio.stories.load_history(), imported['id'])['origin'] is None


def test_live_project_or_character_change_rejects_outdated_answer(studio):
    req = request(studio, 'concepts')
    from manga.artwork_storage import ArtworkStore
    art = ArtworkStore(studio.directory, studio.project_id)
    art.save_character({'name':'新しい人物'}, expected_revision=0)
    before = studio.concepts.path.read_bytes()
    with pytest.raises(ValueError,match='人物設定'):answer(studio,req)
    assert studio.concepts.path.read_bytes()==before


def test_final_confirmation_rejects_story_or_evaluation_changed_in_another_screen(studio):
    version,_=detail(studio)
    evaluation=answer(studio,request(studio,'evaluation'),score=9)
    revision=studio.concepts.load()['revision']
    from manga.editorial_prompts import evaluation_template
    payload=evaluation_template(version['id'])
    studio.editorial.save_evaluation(version['id'],json.dumps(payload),expected_revision=studio.editorial.load()['revision'])
    with pytest.raises(ValueError,match='評価が更新'):
        studio.finalize(expected_revision=revision,expected_version_id=version['id'],expected_evaluation_id=evaluation['id'],reviewed=True)
    latest=studio.stories.save(version['story'],expected_version_id=version['id'])
    with pytest.raises(ValueError,match='脚本が変更'):
        studio.finalize(expected_revision=revision,expected_version_id=version['id'],expected_evaluation_id=evaluation['id'],reviewed=True)
    with pytest.raises(ValueError,match='脚本が変更'):
        studio.restore(version['id'],expected_revision=revision,expected_version_id=version['id'])
    assert studio.editorial.load()['finalizations']==[]


def test_existing_images_composition_and_original_history_are_preserved(tmp_path):
    from review_fixtures import create_review_example
    project,initial,art,pages=create_review_example(tmp_path,page_count=1)
    service=StudioService(tmp_path,project.id)
    protected={path:path.read_bytes() for path in art.root.rglob('*') if path.is_file() and path.name not in ('story_history.json','editorial.json')}
    version,_=detail(service)
    answer(service,request(service,'evaluation'))
    answer(service,request(service,'improvement'))
    assert service.stories.load_history()[0]==initial
    assert all(path.read_bytes()==content for path,content in protected.items())


def test_reevaluation_prompt_contains_previous_script_and_real_changes(studio):
    initial,_=detail(studio)
    answer(studio,request(studio,'evaluation'))
    improved=answer(studio,request(studio,'improvement'))
    req=request(studio,'evaluation')
    assert req['context']['previous_version_id']==initial['id']
    assert req['context']['actual_changes'] and '改善前の脚本' in req['prompt']
    payload=sample_studio_response(req,score=6)
    payload['contradictions']=[dict(description='新しい動作と前後の目的がつながらない',targets=[dict(page=1,panel=1)])]
    studio.import_response(payload,expected_revision=studio.concepts.load()['revision'])
    assert any('新たな矛盾' in n for n in regression_notes(studio.concepts.load(),studio.editorial.load(),studio.stories.load_history(),improved))
