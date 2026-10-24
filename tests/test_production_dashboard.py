import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from manga.artwork_storage import ArtworkStore
from manga.editorial_storage import EditorialStore
from manga.page_storage import PageStore
from manga.page_layout import default_page
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore
from manga.studio_storage import StudioStore
from manga.studio_assets import production_fingerprint
from manga.production_dashboard import dashboard, confirm_delivery, MANUAL_DELIVERY
from manga.production_media import validate_media, evidence
from manga.manual_image_ui import receipt, verify_receipt
from image_ai_fixtures import image_workspace, generate, adopt, references_ready
from video_fixtures import video_workspace


@pytest.fixture
def workspace(tmp_path,manga_project,story):
    ProjectStore(tmp_path).save(manga_project)
    version = StoryStore(tmp_path,manga_project.id).save(story,expected_version_id=None)
    return tmp_path,manga_project,version


def stored_hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


def by_key(result,key):
    return next(r for r in result['rows'] if r['key'] == key)


def test_nine_stages_read_saved_sources_and_do_not_write_progress(workspace):
    directory,project,version = workspace
    before = stored_hashes(directory)
    first = dashboard(project,directory)
    assert len(first['rows']) == 9 and first['next_step']['key'] == 'finalized'
    assert by_key(first,'story')['state'] == '完了'
    assert by_key(first,'directions')['total'] == by_key(first,'images')['total'] == 24
    assert by_key(first,'pages')['total'] == 24
    assert stored_hashes(directory) == before
    editorial = EditorialStore(directory,project.id)
    editorial.finalize(expected_version_id=version['id'],expected_revision=0)
    assert dashboard(project,directory)['next_step']['key'] == 'characters'
    # 同じ本文でも新しい版を取り込んだら、過去版の確定を流用しない。
    StoryStore(directory,project.id).save(version['story'],expected_version_id=version['id'])
    assert by_key(dashboard(project,directory),'finalized')['state'] == '要確認'


def test_partial_adoption_counts_and_reopen_do_not_promote_test_material(image_workspace):
    project,service,_ = image_workspace
    references_ready(image_workspace)
    candidate = generate(image_workspace)
    before = dashboard(project,service.directory)
    assert by_key(before,'images')['done'] == 0
    adopt(image_workspace,candidate)
    current = dashboard(project,service.directory)
    assert by_key(current,'images')['done'] == 1
    assert len(current['test_images']) >= 1
    assert by_key(current,'quality')['state'] != '完了'
    assert current['token'] == dashboard(project,Path(service.directory))['token']
    with pytest.raises(ValueError):
        confirm_delivery(project,service.directory,{k:True for k in MANUAL_DELIVERY},'テスト',expected_revision=0,expected_token=current['token'])


def test_script_version_change_flags_directions_and_images_without_deleting(image_workspace):
    project,service,_ = image_workspace
    references_ready(image_workspace)
    adopt(image_workspace,generate(image_workspace))
    art_before = service.artwork.path.read_bytes()
    latest = service.store.stories.load_history()[-1]
    service.store.stories.save(latest['story'],expected_version_id=latest['id'])
    result = dashboard(project,service.directory)
    assert by_key(result,'directions')['state'] == by_key(result,'images')['state'] == '要確認'
    assert result['panels'][0]['image_stale'] and result['panels'][0]['direction_stale']
    assert service.artwork.path.read_bytes() == art_before


@pytest.mark.parametrize('field,value', [('page',2),('panel',3),('story_version_id','wrong'),('character_ids',[]),('bible_ids',[]),('prompt_sha256','wrong')])
def test_manual_receipt_rejects_wrong_target_person_or_script(image_workspace,field,value):
    _,service,_ = image_workspace
    plan = service.plan('panel',page=1,panel=1)
    ticket = receipt(plan)
    ticket[field] = value
    with pytest.raises(ValueError,match='一致しません'):
        verify_receipt(plan,json.dumps(ticket).encode())
    assert not service.store.load()['jobs']


def test_manual_receipt_roundtrip_and_invalid_input(image_workspace):
    _,service,_ = image_workspace
    plan = service.plan('panel',page=1,panel=1)
    assert verify_receipt(plan,json.dumps(receipt(plan)).encode())
    for raw in (b'bad',b'x'*65537):
        with pytest.raises(ValueError): verify_receipt(plan,raw)


def save_outputs(workspace):
    directory,project,version = workspace
    pages = PageStore(directory,project.id)
    for page in version['story']['pages']:
        pages.save_page(page['page'],default_page(project.id,page,version['id']),story_version_id=version['id'],expected_revision=pages.load()['revision'])
    art = ArtworkStore(directory,project.id).load()
    fp = production_fingerprint(version,art,pages.load(),directory/project.id)
    studio = StudioStore(directory,project.id)
    events = []
    for kind in ('png_zip','pdf'):
        # Storeの耐障害性テスト用バイト列。実形式は連携テストで描画する。
        events.append(studio.save_export(dict(kind=kind,pages=list(range(1,9)),filename=kind,fingerprint=fp),kind.encode()))
    return studio,events


def test_png_and_pdf_separately_counted_and_missing_files_detected(workspace):
    directory,project,_ = workspace
    studio,events = save_outputs(workspace)
    result = dashboard(project,directory)
    assert by_key(result,'pages')['done'] == 24
    # 両形式があっても、採用画像のない作品は完了にならない。
    assert by_key(result,'pages')['state'] != '完了'
    studio.artifact_path(events[-1]).unlink()
    result = dashboard(project,directory)
    assert by_key(result,'pages')['done'] == 16 and result['output_errors']
    assert len(studio.load()['exports']) == 2


def test_page_output_reuse_metadata_failure_and_hash_validation(workspace,monkeypatch):
    studio,events = save_outputs(workspace)
    event = {k:v for k,v in events[0].items() if k not in ('path','sha256','bytes','saved_at')}
    studio.save_export(event,b'png_zip')
    assert len(studio.load()['exports']) == 2
    previous = studio.path.read_bytes()
    original = __import__('manga.studio_storage',fromlist=['write_json_atomic']).write_json_atomic
    monkeypatch.setattr('manga.studio_storage.write_json_atomic',lambda *a,**kw:(_ for _ in ()).throw(StorageError('disk full')))
    with pytest.raises(StorageError): studio.save_export(event,b'new fixture')
    assert studio.path.read_bytes() == previous
    monkeypatch.setattr('manga.studio_storage.write_json_atomic',original)
    studio.save_export(event,b'new fixture')
    assert len(studio.load()['exports']) == 3
    studio.artifact_path(events[0]).write_bytes(b'changed')
    with pytest.raises(StorageError): studio.export_bytes(events[0])


def test_corrupt_store_is_not_reset_or_reported_complete(workspace):
    directory,project,_ = workspace
    path = directory/project.id/'voice.json'
    path.write_text('{broken')
    with pytest.raises(StorageError): dashboard(project,directory)
    assert path.read_text() == '{broken'


def test_media_validation_persists_and_detects_subtitle_change(video_workspace):
    service,timeline = video_workspace
    result = validate_media(service.directory,service.project_id)
    assert result['timeline_id'] == timeline['id'] and result['audio_done'] == 4 and result['mock_audio']
    assert result['export_ids'] == [] and result['errors']
    assert evidence(service.directory,service.project_id)['id'] == result['id']
    assert validate_media(service.directory,service.project_id)['id'] == result['id']
    record = service.voice.store.load()['timelines'][-1]
    path = service.voice.store.safe_path(record['paths']['srt'])
    path.write_text('字幕の取り違え')
    assert evidence(service.directory,service.project_id) is None
    after = validate_media(service.directory,service.project_id)
    assert after['timeline_id'] is None and any('字幕' in e for e in after['errors'])


def test_missing_wav_invalidates_validation_and_history_survives(video_workspace):
    service,timeline = video_workspace
    validate_media(service.directory,service.project_id)
    original = service.voice.store.path.read_bytes()
    service.voice.store.safe_path(timeline['assets'][0]['path']).unlink()
    assert evidence(service.directory,service.project_id) is None
    result = validate_media(service.directory,service.project_id)
    assert result['audio_done'] < 4 and not result['timeline_id']
    assert service.voice.store.path.read_bytes() == original


def test_dashboard_does_not_read_or_generate_hidden_original_images(image_workspace):
    project,service,_ = image_workspace
    before = stored_hashes(service.store.root)
    with patch.object(ArtworkStore,'image_bytes',side_effect=AssertionError('hidden original read')),patch.object(type(service),'plan',side_effect=AssertionError('implicit generation plan')):
        result = dashboard(project,service.directory)
    assert result['rows'] and stored_hashes(service.store.root) == before


def test_pending_image_adoption_routes_to_existing_recovery(image_workspace,monkeypatch):
    project,service,_ = image_workspace
    candidate = generate(image_workspace)
    original = service.artwork.select_panel_image
    monkeypatch.setattr(service.artwork,'select_panel_image',lambda *a,**kw:(_ for _ in ()).throw(StorageError('interrupted')))
    with pytest.raises(StorageError): adopt(image_workspace,candidate)
    result = dashboard(project,service.directory)
    assert any(r['section'] == 'AI画像生成・採用' for r in result['recoveries'])
    assert not next(ok for label,ok,_ in result['checks'] if label == '保存途中・実行中ジョブの解消')
    before = len(service.artwork.load()['panels']['1:1']['candidates'])
    monkeypatch.setattr(service.artwork,'select_panel_image',original)
    service.recover_adoption(confirmed=True)
    assert len(service.artwork.load()['panels']['1:1']['candidates']) == before
    assert not dashboard(project,service.directory)['recoveries']


def test_missing_reference_and_new_bible_are_not_complete(image_workspace):
    project,service,characters = image_workspace
    references_ready(image_workspace)
    adopt(image_workspace,generate(image_workspace))
    from manga.image_ai_samples import confirm_bible
    person = service.artwork.load()['characters'][0]
    confirm_bible(characters,person['id'])
    assert by_key(dashboard(project,service.directory),'images')['state'] == '要確認'
    reference = person['references'][0]
    service.artwork.image_path(reference).unlink()
    assert by_key(dashboard(project,service.directory),'characters')['state'] == '要確認'


def test_invalid_video_evidence_fails_closed(workspace):
    directory,project,_ = workspace
    from manga.video_storage import VideoStore
    store = VideoStore(directory,project.id)
    data = store.load()
    store.save(data,'production_validation',token='wrong')
    before = store.path.read_bytes()
    with pytest.raises(StorageError): dashboard(project,directory)
    assert store.path.read_bytes() == before
