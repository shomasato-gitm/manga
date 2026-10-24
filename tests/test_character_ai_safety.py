import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from manga.budget.config import open_budget
from manga.character_ai_assets import reference_path
from manga.storage import StorageError, write_json_atomic
from character_ai_fixtures import character_workspace, generate, adopt, bible, hashes


@pytest.mark.parametrize('fail_target', ['artwork.json', 'assistant.json', 'done'])
def test_adoption_failure_recovers_once_preserving_assets(character_workspace, monkeypatch, fail_target):
    _, service = character_workspace
    result = generate(character_workspace)
    person = service.artwork.load()['characters'][0]
    import manga.assistant_repository as module
    original = module.write_json_atomic
    failed = []
    def fail(path, data):
        if not failed and (path.name == fail_target or fail_target == 'done' and path.name == 'assistant_pending.json' and data.get('status') == 'done'):
            failed.append(True); raise OSError('simulated interrupted adoption')
        return original(path, data)
    before = hashes(service.store.root)
    monkeypatch.setattr(module, 'write_json_atomic', fail)
    with pytest.raises((OSError, StorageError)):
        adopt(character_workspace, result, existing_id=person['id'], fields=['visual_identity'])
    monkeypatch.setattr(module, 'write_json_atomic', original)
    assert service.proposals.pending()
    service.proposals.resume(); service.proposals.resume()
    art = service.artwork.load()
    assert len(art['characters']) == 1 and art['characters'][0]['id'] == person['id']
    assert art['characters'][0]['references'] == person['references']
    after = hashes(service.store.root)
    assert all(after[k] == v for k,v in before.items() if k not in ('assistant.json','artwork.json','assistant_pending.json'))
    assert len(service.proposals.load()['adoptions']) == 1


def leave_pending(workspace, monkeypatch):
    _, service = workspace
    result = generate(workspace)
    import manga.assistant_repository as module
    original = module.write_json_atomic
    def fail(path, data):
        if path.name == 'assistant.json': raise OSError('simulated metadata failure')
        return original(path, data)
    monkeypatch.setattr(module, 'write_json_atomic', fail)
    with pytest.raises((OSError, StorageError)): adopt(workspace, result, 1)
    monkeypatch.setattr(module, 'write_json_atomic', original)
    return service


def test_conflicting_edit_stops_recovery_without_overwrite(character_workspace, monkeypatch):
    service = leave_pending(character_workspace, monkeypatch)
    service.artwork.save_style('手動の別編集', expected_revision=service.artwork.load()['revision'])
    before = service.artwork.path.read_bytes()
    with pytest.raises(StorageError, match='別の編集'): service.proposals.resume()
    assert service.artwork.path.read_bytes() == before


def test_tampered_journal_cannot_drop_existing_person_or_images(character_workspace, monkeypatch):
    service = leave_pending(character_workspace, monkeypatch)
    path = service.proposals.journal_path
    journal = json.loads(path.read_text())
    journal['after_target']['characters'] = journal['after_target']['characters'][1:]
    journal['after_target']['panels'] = {}
    write_json_atomic(path, journal)
    before = service.artwork.path.read_bytes()
    with pytest.raises(StorageError, match='保持条件'): service.proposals.resume()
    assert service.artwork.path.read_bytes() == before


def test_stale_revisions_and_story_adoption_are_rejected(character_workspace):
    _, service = character_workspace
    result = generate(character_workspace)
    person = service.artwork.load()['characters'][0]
    with pytest.raises(StorageError, match='更新'):
        service.proposals.adopt_fields(result['proposal']['id'], 'character-1', result['proposal']['payload']['items'][0]['fields'], ['visual_identity'], person['id'],
            expected_revision=0, expected_artwork_revision=service.artwork.load()['revision'], expected_editorial_revision=1, expected_character_revision=0, identity_confirmed=True)
    version = service.proposals.latest(); service.stories.save(version['story'], expected_version_id=version['id'])
    before = service.artwork.path.read_bytes()
    with pytest.raises(StorageError, match='ストーリー'): adopt(character_workspace, result, existing_id=person['id'])
    assert service.artwork.path.read_bytes() == before


@pytest.mark.parametrize('kind', ['file_link', 'directory_link', 'escape', 'bad_id'])
def test_reference_path_safety(character_workspace, tmp_path, kind):
    _, service = character_workspace
    person = service.artwork.load()['characters'][0]
    ref = copy.deepcopy(person['references'][0])
    path = reference_path(service.store.root, ref)
    original = path.read_bytes()
    if kind == 'file_link':
        outside = tmp_path/'outside.png'; outside.write_bytes(original)
        path.unlink(); path.symlink_to(outside)
    elif kind == 'directory_link':
        original_dir = path.parent
        moved = tmp_path/'moved_refs'; original_dir.rename(moved); original_dir.symlink_to(moved, target_is_directory=True)
    elif kind == 'escape': ref['path'] = '../../outside.png'
    else: ref['id'] = 'not-an-id'
    with pytest.raises((ValueError, StorageError)): reference_path(service.store.root, ref)
    if kind in ('file_link', 'directory_link'):
        check = service.run_checks(expected_revision=0)
        assert any(i['kind'] == 'unsafe_reference' for i in check['issues'])


@pytest.mark.parametrize('filename', ['artwork.json', 'assistant.json', '.assistant.lock', 'character_ai.json'])
def test_source_and_lock_symlinks_rejected(character_workspace, tmp_path, filename):
    _, service = character_workspace
    path = service.store.root/filename
    target = tmp_path/'external.json'; target.write_bytes(path.read_bytes() if path.exists() else b'{}')
    if path.exists(): path.unlink()
    path.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(StorageError): generate(character_workspace)
    assert target.read_bytes() == before


@pytest.mark.parametrize('mutation', ['schema', 'duplicate', 'reference_id', 'path'])
def test_invalid_bible_json_never_initializes_or_overwrites(character_workspace, mutation):
    _, service = character_workspace
    bible(character_workspace)
    value = json.loads(service.store.path.read_text())
    if mutation == 'schema': value['schema_version'] = 2
    elif mutation == 'duplicate': value['bibles'].append(copy.deepcopy(value['bibles'][0]))
    elif mutation == 'reference_id': value['bibles'][0]['primary_reference_id'] = str(uuid4())
    else: value['bibles'][0]['references'][0]['path'] = '../../other.png'
    write_json_atomic(service.store.path, value)
    before = hashes(service.store.root)
    with pytest.raises(StorageError): service.store.load()
    assert hashes(service.store.root) == before


@pytest.mark.parametrize('uncertain', [False, True])
def test_mock_never_sends_or_changes_shared_exhausted_budget(character_workspace, monkeypatch, uncertain):
    _, service = character_workspace
    ledger = open_budget(service.store.root.parent)
    identifier = str(uuid4())
    ledger.reserve(project_id=service.project_id, request_id=identifier, provider='test', model='fixture', kind='test', max_jpy='500')
    if uncertain: ledger.mark_uncertain(identifier)
    before = ledger.path.read_bytes()
    monkeypatch.setenv('OPENAI_API_KEY','dummy-sensitive-value-never-log')
    monkeypatch.setenv('MANGA_ENABLE_PAID_CHARACTER','1')
    import manga.story_ai_provider as provider
    def forbidden(*args, **kwargs): raise AssertionError('Paid transport must never be called')
    monkeypatch.setattr(provider, 'transport', forbidden)
    result = generate(character_workspace)
    assert result['request']['character_ai']['api_cost_jpy'] == 0 and ledger.path.read_bytes() == before
    assert 'dummy-sensitive' not in service.proposals.path.read_text()


def test_concurrent_generation_creates_only_one_request(character_workspace):
    project, service = character_workspace
    version = service.proposals.latest()['id']
    def call():
        try: return service.proposals.create_mock(project, version, expected_revision=0)
        except StorageError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _:call(), range(2)))
    assert len(service.proposals.load()['requests']) == 1 and len(service.proposals.load()['proposals']) == 1
    assert any(results)


def test_bible_write_failure_preserves_previous_version(character_workspace, monkeypatch):
    _, service = character_workspace
    bible(character_workspace)
    import manga.character_ai_storage as storage
    def fail(*args, **kwargs): raise OSError('fixture disk full')
    monkeypatch.setattr(storage, 'write_json_atomic', fail)
    before = service.store.path.read_bytes()
    with pytest.raises((OSError, StorageError)): bible(character_workspace)
    assert service.store.path.read_bytes() == before


def test_missing_bible_metadata_with_initialization_record_never_resets(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    service.store.path.unlink()  # fixture loss only
    before = hashes(service.store.root)
    with pytest.raises(StorageError, match='初期化せず'): service.store.load()
    assert hashes(service.store.root) == before


def test_missing_assistant_after_adoption_never_reinitializes(character_workspace):
    _, service = character_workspace
    result = generate(character_workspace); adopt(character_workspace, result, 1)
    service.proposals.path.unlink()  # fixture loss only
    before = hashes(service.store.root)
    with pytest.raises(StorageError, match='欠損'): generate(character_workspace)
    assert hashes(service.store.root) == before


def test_missing_artwork_with_existing_images_never_initializes_empty_characters(character_workspace):
    _, service = character_workspace
    generate(character_workspace)
    service.artwork.path.unlink()  # fixture loss only
    before = hashes(service.store.root)
    with pytest.raises(StorageError, match='artwork.json'): generate(character_workspace)
    assert hashes(service.store.root) == before
