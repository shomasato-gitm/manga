import json

import pytest

from manga.github_sync import SyncService
from manga.sync_manifest import SyncConflict, decode_json
from manga.sync_responses import fetch_response, accept_response
from manga.story_studio import StudioService
from manga.story_studio_samples import sample_studio_response
from manga.story_studio_status import lineage, exact_evaluation
from test_github_sync import local_remote
from test_story_studio import studio, request, answer, detail, add_reference


def publish(sync):
    plan = sync.prepare_export('exchange', expected_revision=sync.store.load()['revision'])
    sync.push(plan['id'], expected_revision=sync.store.load()['revision'])
    return plan


def test_concept_only_snapshot_preserves_three_options_and_selection_after_explicit_adoption(studio, local_remote, tmp_path):
    req = request(studio, 'concepts')
    batch = answer(studio, req)
    studio.select(batch['id'], batch['payload']['concepts'][2]['id'], expected_revision=studio.concepts.load()['revision'])
    source = SyncService(studio.directory, studio.project_id, local_remote)
    plan = publish(source)
    assert plan['manifest']['story_version_id'] is None
    assert plan['manifest']['content_kind'] == 'concepts'
    receiver = SyncService(tmp_path/'receiver', studio.project_id, local_remote)
    candidate = receiver.fetch_bundle('exchange', expected_revision=0)
    target = StudioService(receiver.directory, studio.project_id)
    assert target.concepts.load()['batches'] == []
    receiver.adopt(candidate['id'], ['concepts'], expected_revision=1)
    result = target.concepts.load()
    assert len(result['batches'][0]['payload']['concepts']) == 3
    assert result['selected_id'] == studio.concepts.load()['selected_id']
    assert target.stories.load_history() == []


def test_full_pdca_roundtrip_keeps_scores_changes_origin_and_requires_local_final_confirmation(studio, local_remote, tmp_path):
    ref = add_reference(studio)
    initial, req = detail(studio, {ref['id']: ['opening']})
    for score in (6, 7, 8):
        answer(studio, request(studio, 'evaluation'), score=score)
        answer(studio, request(studio, 'improvement'))
    answer(studio, request(studio, 'evaluation'), score=9)
    studio.finalize(expected_version_id=studio.stories.load_history()[-1]['id'], expected_evaluation_id=studio.editorial.load()['evaluations'][-1]['id'], expected_revision=studio.concepts.load()['revision'], reviewed=True)
    source = SyncService(studio.directory, studio.project_id, local_remote)
    publish(source)
    receiver = SyncService(tmp_path/'receiver', studio.project_id, local_remote)
    candidate = receiver.fetch_bundle('exchange', expected_revision=0)
    receiver.adopt(candidate['id'], ['story', 'concepts'], expected_revision=1)
    target = StudioService(receiver.directory, studio.project_id)
    data, history = target.concepts.load(), target.stories.load_history()
    assert len(history) == 4 and len(data['improvements']) == 3
    assert all(row['changes'] for row in data['improvements'])
    assert lineage(data, history, history[-1]['id'])['origin']['request_id'] == req['id']
    assert exact_evaluation(target.editorial.load(), history[-1])['payload']['total_score'] == 90
    assert target.editorial.load()['finalizations'] == []
    assert data['finalizations']  # 元端末の確認は履歴として保持する。
    restored = target.restore(initial['id'], expected_version_id=target.stories.load_history()[-1]['id'], expected_revision=data['revision'])
    assert exact_evaluation(target.editorial.load(), restored) is None


def test_github_overview_response_stays_inbox_until_manual_import(studio, local_remote):
    req = request(studio, 'concepts')
    path = f'works/{studio.project_id}/responses/concepts.json'
    local_remote.publish('exchange', None, None, {path: json.dumps(sample_studio_response(req)).encode()}, 'Mock ChatGPT response')
    sync = SyncService(studio.directory, studio.project_id, local_remote)
    candidate = fetch_response(sync, 'exchange', path, 'story_studio', expected_revision=0)
    assert studio.concepts.load()['responses'] == []
    accept_response(sync, candidate['id'], expected_revision=1)
    data = studio.concepts.load()
    assert data['responses'][0]['status'] == '確認待ち' and data['batches'] == []
    studio.import_response(data['responses'][0]['payload'], expected_revision=data['revision'])
    assert len(studio.concepts.load()['batches']) == 1
    assert studio.concepts.load()['selected_id'] is None
    assert studio.stories.load_history() == []


def test_concept_local_change_after_fetch_stops_sync_without_losing_history(studio, local_remote, tmp_path):
    answer(studio, request(studio, 'concepts'))
    source = SyncService(studio.directory, studio.project_id, local_remote)
    publish(source)
    receiver = SyncService(tmp_path/'receiver', studio.project_id, local_remote)
    initial = receiver.fetch_bundle('exchange', expected_revision=0)
    receiver.adopt(initial['id'], ['concepts'], expected_revision=1)
    incoming = receiver.fetch_bundle('exchange', expected_revision=2)
    target = StudioService(receiver.directory, studio.project_id)
    target.set_policy(90, 4, expected_revision=target.concepts.load()['revision'])
    before = target.concepts.path.read_bytes()
    with pytest.raises(SyncConflict):receiver.adopt(incoming['id'], ['concepts'], expected_revision=3)
    assert target.concepts.path.read_bytes() == before


def test_tampered_same_request_id_does_not_overwrite_existing_records(studio, local_remote, tmp_path):
    answer(studio, request(studio, 'concepts'))
    source = SyncService(studio.directory, studio.project_id, local_remote)
    publish(source)
    receiver = SyncService(tmp_path/'receiver', studio.project_id, local_remote)
    candidate = receiver.fetch_bundle('exchange', expected_revision=0)
    receiver.adopt(candidate['id'], ['concepts'], expected_revision=1)
    altered = studio.concepts.load()
    altered['requests'][0]['prompt'] += '\n異なる依頼内容'
    studio.concepts.path.write_text(json.dumps(altered), encoding='utf-8')
    publish(source)
    incoming = receiver.fetch_bundle('exchange', expected_revision=2)
    before = (receiver.store.root/'story_concepts.json').read_bytes()
    with pytest.raises(SyncConflict):receiver.adopt(incoming['id'], ['concepts'], expected_revision=3)
    assert (receiver.store.root/'story_concepts.json').read_bytes() == before


def test_false_change_history_or_missing_evaluation_is_not_exported(studio,local_remote):
    detail(studio)
    answer(studio,request(studio,'evaluation'))
    answer(studio,request(studio,'improvement'))
    sync=SyncService(studio.directory,studio.project_id,local_remote)
    altered=studio.concepts.load()
    altered['improvements'][0]['changes']=[]
    studio.concepts.path.write_text(json.dumps(altered),encoding='utf-8')
    before=studio.stories.path.read_bytes()
    with pytest.raises(ValueError,match='実際の変更'):sync.prepare_export('exchange',expected_revision=0)
    assert studio.stories.path.read_bytes()==before and sync.store.load()['exports']==[]


def test_missing_editorial_history_rejects_pdca_snapshot(studio):
    from manga.sync_local import collect_local
    from manga.sync_manifest import build_manifest,validate_bundle
    detail(studio)
    answer(studio,request(studio,'evaluation'))
    files=collect_local(studio.directory,studio.project_id);files.pop('editorial.json')
    manifest=build_manifest(studio.project_id,files,story_version_id=studio.stories.load_history()[-1]['id'],source_commit='a'*40)
    with pytest.raises(ValueError,match='評価履歴'):validate_bundle(manifest,files,studio.project_id)


def test_concept_only_sync_can_keep_character_ids_and_reference_images_before_story(studio,local_remote,tmp_path):
    from manga.artwork_storage import ArtworkStore
    from review_fixtures import sample_image
    art=ArtworkStore(studio.directory,studio.project_id)
    character=art.save_character({'name':'既存の主人公','hair_color':'黒'},expected_revision=0)
    image=art.add_reference(character['id'],sample_image(),'front.png','正面',expected_revision=1)
    art.select_reference(character['id'],image['id'],expected_revision=2)
    batch=answer(studio,request(studio,'concepts'))
    studio.select(batch['id'],batch['payload']['concepts'][0]['id'],expected_revision=studio.concepts.load()['revision'])
    source=SyncService(studio.directory,studio.project_id,local_remote);publish(source)
    receiver=SyncService(tmp_path/'receiver',studio.project_id,local_remote)
    candidate=receiver.fetch_bundle('exchange',expected_revision=0)
    receiver.adopt(candidate['id'],['concepts','settings','images'],expected_revision=1)
    target=StudioService(receiver.directory,studio.project_id)
    other=ArtworkStore(receiver.directory,studio.project_id)
    assert other.load()['characters'][0]['id']==character['id']
    assert other.load()['characters'][0]['primary_reference_id']==image['id']
    assert other.image_bytes(image)==art.image_bytes(image)
    detail_req=request(target,'detail')
    answer(target,detail_req)
    assert len(target.stories.load_history())==1
