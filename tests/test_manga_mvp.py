"""新規作品の漫画一括制作。API・ネットワークはフェイク以外拒否する。"""
import io
import socket
import zipfile
from uuid import uuid4

import pytest
from PIL import Image

from manga.manga_mvp import MangaMVP, create, creation_plan
from manga.manga_mvp_steps import waiting_images
from manga.image_ai_models import REVIEW_FIELDS
from manga.storage import StorageError


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('Network forbidden'))
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('Network forbidden'))
    monkeypatch.setattr('manga.budget.BudgetedExecutor.run', lambda *a, **k: pytest.fail('No paid operation in mock mode'))
    directory = tmp_path / 'projects'
    plan = creation_plan(directory, dict(theme='桜色のタイムカプセル', genre='青春', page_count=8, style='白黒の日本漫画'), str(uuid4()))
    project = create(directory, plan, approved=True)
    assert create(directory, plan, approved=True).id == project.id
    return MangaMVP(directory, project.id)


def run(engine, limit=128):
    return engine.run(engine.plan()['token'], approved=True, limit=limit)


def review(engine):
    engine.review_images(engine.plan()['token'], {key:True for key in REVIEW_FIELDS}, 'テスト画像だけの動作確認。実人物一致・公開権は保証しない。')


def test_eight_pages_twenty_four_panels_real_store_chain_resume_and_outputs(engine):
    plan = run(engine)
    assert len(waiting_images(engine)) == 2
    assert not engine.images.store.load()['adoptions']
    assert '確認' in plan['reason']
    history = engine.story.stories.load_history()
    assert len(history) == 1 and len(history[0]['story']['pages']) == 8
    assert sum(len(p['panels']) for p in history[0]['story']['pages']) == 24
    people = engine.story.artwork.load()['characters']
    assert len(people) == 2
    ids = [p['id'] for p in people]
    review(engine)
    assert not engine.images.store.load()['adoptions']  # 確認だけでは採用しない
    run(engine)
    assert len(waiting_images(engine)) == 24
    before = engine.story.artwork.path.read_bytes()
    assert len(engine.images.store.load()['adoptions']) == 2
    reopened = MangaMVP(engine.directory, engine.project_id)
    assert reopened.story.artwork.path.read_bytes() == before
    review(reopened)
    plan = run(reopened)
    assert plan['reason'] == 'outputs_ready'
    assert [p['id'] for p in reopened.story.artwork.load()['characters']] == ids
    assert len(reopened.images.store.load()['adoptions']) == 26
    outputs = reopened.studio.load()['exports']
    assert [e['kind'] for e in outputs].count('page') == 8
    for event in outputs:
        content = reopened.studio.export_bytes(event)
        if event['kind'] == 'page':
            with Image.open(io.BytesIO(content)) as image:
                assert image.format == 'PNG' and image.width > 0
        elif event['kind'] == 'pdf':
            assert content.startswith(b'%PDF')
            from pypdf import PdfReader
            assert len(PdfReader(io.BytesIO(content)).pages) == 8
        elif event['kind'] == 'png_zip':
            assert len(zipfile.ZipFile(io.BytesIO(content)).namelist()) == 8
    counts = (len(reopened.images.store.load()['jobs']), len(outputs), len(reopened.story.store.load()['requests']))
    assert run(reopened)['reason'] == 'outputs_ready'
    assert counts == (len(reopened.images.store.load()['jobs']), len(reopened.studio.load()['exports']), len(reopened.story.store.load()['requests']))
    assert all(c['mock'] for c in reopened.images.store.load()['candidates'])


def test_image_adoption_journal_resumes_same_registered_id(engine, monkeypatch):
    run(engine)
    review(engine)
    original = engine.images.store.save
    def fail(data, kind, **fields):
        if kind == 'image_adopted':
            raise OSError('disk failure')
        return original(data, kind, **fields)
    monkeypatch.setattr(engine.images.store, 'save', fail)
    with pytest.raises(ValueError):
        run(engine, limit=1)
    assert engine.plan()['action']['kind'] == 'recover_image'
    before = [r['id'] for c in engine.story.artwork.load()['characters'] for r in c['references']]
    assert len(before) == 1
    monkeypatch.setattr(engine.images.store, 'save', original)
    run(engine, limit=1)
    assert [r['id'] for c in engine.story.artwork.load()['characters'] for r in c['references']] == before
    assert len(engine.images.store.load()['adoptions']) == 1
    run(engine, limit=6)
    assert len(engine.images.store.load()['adoptions']) == 2
    assert len(engine.images.store.load()['jobs']) == 2


def test_unknown_speaker_stops_before_character_registration(engine, monkeypatch):
    from manga.story_ai_mock import MockStoryAI
    import json
    original = MockStoryAI.generate
    def unknown(self, request):
        response = original(self, request)
        if request['kind'] == 'draft':
            payload = json.loads(response['text'])
            payload['story']['pages'][0]['panels'][0]['dialogue'] = '彼：「誰だったかな。」'
            response['text'] = json.dumps(payload, ensure_ascii=False)
        return response
    monkeypatch.setattr(MockStoryAI, 'generate', unknown)
    plan = run(engine)
    assert '話者が不明' in plan['reason']
    assert not engine.story.artwork.load()['characters']
    assert not engine.images.store.load()['jobs']


def test_stop_request_is_kept_and_explicit_resume_reuses_results(engine, monkeypatch):
    from manga import manga_mvp_steps
    original = manga_mvp_steps.execute
    def stop_after_one(service, item):
        service.stop()
        original(service, item)
    monkeypatch.setattr(manga_mvp_steps, 'execute', stop_after_one)
    run(engine)
    assert engine.load()['stop_requested']
    assert len(engine.story.store.load()['concept_batches']) == 1
    assert not engine.story.store.load()['candidates']
    monkeypatch.setattr(manga_mvp_steps, 'execute', original)
    run(engine, limit=1)
    assert len(engine.story.store.load()['concept_batches']) == 1


def test_image_comparison_is_not_an_execution_quote():
    from manga.manga_mvp_costs import image_comparison
    rows = image_comparison()
    assert rows[0]['panels_usd'] == '0.8064' and rows[0]['total_usd'] == '0.8736'
    assert all(r['network_enabled'] is False for r in rows)
    assert rows[-1]['per_image_usd'] == '未確認'


def test_approval_stale_token_lock_and_changed_sources(engine):
    plan = engine.plan()
    with pytest.raises(ValueError):
        engine.run(plan['token'])
    with engine.automation.lock(), pytest.raises(StorageError):
        engine.run(plan['token'], approved=True)
    run(engine, limit=1)
    with pytest.raises(StorageError):
        engine.run(plan['token'], approved=True)
    engine.story.artwork.save_style('手動で修正した画風', expected_revision=engine.story.artwork.load()['revision'])
    assert engine.plan()['changed']
    before = engine.story.artwork.path.read_bytes()
    with pytest.raises(StorageError):
        run(engine)
    assert engine.story.artwork.path.read_bytes() == before
    engine.reinspect(engine.plan()['token'], confirmed=True)
    assert not engine.plan()['changed']


def test_save_failure_reuses_completed_job_and_no_error_details(engine, monkeypatch):
    original = engine.update
    failed = False
    def fail(**changes):
        nonlocal failed
        if not failed and any(e['state'] == 'done' for e in changes.get('events', [])):
            failed = True
            raise OSError('SECRET-looking raw details')
        return original(**changes)
    monkeypatch.setattr(engine, 'update', fail)
    with pytest.raises(OSError):
        run(engine)
    assert len(engine.story.store.load()['concept_batches']) == 1
    monkeypatch.setattr(engine, 'update', original)
    # 保存済みの出力を再検査し、未送信扱いで再生成しない。
    assert engine.plan()['changed'] is False  # 候補の正本はsourceの脚本・人物には含めない
    run(engine, limit=1)
    assert len(engine.story.store.load()['concept_batches']) == 1


def test_existing_work_and_shared_ledger_untouched_by_mock(engine, tmp_path, monkeypatch):
    from manga.models import Project
    from manga.storage import ProjectStore
    store = ProjectStore(engine.directory)
    other = store.save(Project(title='既存作品', genre='日常', synopsis='保持'))
    path = engine.directory / (other.id + '.json')
    before = path.read_bytes()
    ledger = tmp_path / 'budget.sqlite3'
    ledger.write_bytes(b'existing shared ledger')
    monkeypatch.setenv('MANGA_BUDGET_DB', str(ledger))
    run(engine, limit=2)
    assert path.read_bytes() == before and ledger.read_bytes() == b'existing shared ledger'
    plan = creation_plan(engine.directory, dict(theme='別作品', genre='青春', page_count=8, style='漫画'), str(uuid4()))
    plan['project_id'] = other.id
    # 対象変更した見積は承認と一致せず、既存作品を上書きしない。
    with pytest.raises(ValueError):
        create(engine.directory, plan, approved=True)
    assert path.read_bytes() == before
