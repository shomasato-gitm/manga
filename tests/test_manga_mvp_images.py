"""実HTTPなし。生成画像はフェイクであり人物一致・公開品質を保証しない。"""
import io
import socket
from uuid import uuid4

import pytest
from pypdf import PdfReader

from gemini_image_fixtures import card, FakeImage
from manga.image_ai_live_provider import GeminiImageAdapter, LiveError
from manga.image_ai_models import REVIEW_FIELDS
from manga.manga_mvp import MangaMVP, creation_plan, create
from manga.manga_mvp_steps import waiting_images
from manga.storage import StorageError


@pytest.fixture
def live_mvp(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('Network forbidden'))
    directory = tmp_path / 'projects'
    monkeypatch.setenv('MANGA_BUDGET_DB', str(tmp_path / 'budget.sqlite3'))
    monkeypatch.setenv('GEMINI_API_KEY', 'FAKE-MVP-IMAGE-KEY-NEVER-PERSIST')
    monkeypatch.setenv('MANGA_ENABLE_TEXT_API', '1')
    from test_gemini35 import card35
    plan = creation_plan(directory, dict(theme='桜色のタイムカプセル', genre='青春', page_count=8, style='日本漫画'),
                         str(uuid4()), mode='verified-free', card=card35(free=True), image_config=card())
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    fake = FakeImage()
    engine.images._live_adapter = GeminiImageAdapter(fake_transport=fake)
    from test_manga_mvp_gemini import fake_http
    # この既存フェイクだけが認証ヘッダーをメモリー内で検証する。ファイルへ記録しない。
    monkeypatch.setenv('GEMINI_API_KEY', 'FAKE-MVP-KEY-NEVER-PERSIST')
    engine.test_text_calls = fake_http(engine, monkeypatch)
    return engine, fake


def run(engine, limit=128):
    return engine.run(engine.plan()['token'], approved=True, limit=limit)


def review(engine):
    engine.review_images(engine.plan()['token'], {k:True for k in REVIEW_FIELDS}, 'フェイク画像・権利確認の試験。公開不可。')


def approve(engine):
    value = engine.image_preview()
    assert value and value['possible']
    engine.approve_images(value['token'], confirmed=True)
    return value


def first(engine):
    run(engine)
    assert engine.image_preview()['count'] == 1
    approve(engine)
    run(engine)
    assert len(waiting_images(engine)) == 1
    review(engine)
    candidate = engine.images.store.load()['candidates'][0]
    engine.confirm_first_image(candidate['id'], confirmed=True, test_only=True)


def panels(engine):
    first(engine)
    assert approve(engine)['count'] == 1
    run(engine)
    review(engine)
    run(engine)
    assert engine.image_preview()['count'] == 24


def test_twenty_six_fake_images_reference_linkage_restart_and_png_pdf(live_mvp):
    engine, fake = live_mvp
    panels(engine)
    assert len(fake.calls) == 2
    scope = approve(engine)
    assert scope['count'] == 24
    run(engine)
    assert len(fake.calls) == 26 and len(waiting_images(engine)) == 24
    assert len(engine.test_text_calls) == 3
    assert len(engine.images.store.load()['adoptions']) == 2
    data = engine.images.store.load()
    references = {r['id']:p['id'] for p in engine.story.artwork.load()['characters'] for r in p['references']}
    for job in data['jobs'][2:]:
        assert job['plan']['kind'] == 'panel'
        assert job['plan']['reference_images']
        assert job['live']['scope_id']
        for ref in job['plan']['reference_images']:
            assert references[ref['id']] == ref['character_id']
    assert all(any('inlineData' in p for p in request['contents'][0]['parts']) for request in fake.calls[2:])
    reopened = MangaMVP(engine.directory, engine.project_id)
    reopened.images._live_adapter = engine.images.live_adapter
    review(reopened)
    assert run(reopened)['reason'] == 'outputs_ready'
    assert len(fake.calls) == 26 and len(reopened.images.store.load()['adoptions']) == 26
    outputs = reopened.studio.load()['exports']
    assert sum(e['kind'] == 'page' for e in outputs) == 8
    pdf = next(e for e in outputs if e['kind'] == 'pdf')
    assert len(PdfReader(io.BytesIO(reopened.studio.export_bytes(pdf))).pages) == 8
    assert run(reopened)['reason'] == 'outputs_ready' and len(fake.calls) == 26
    from manga.image_ai_live_plan import shared_ledger
    assert not shared_ledger(engine.images).summary(engine.project_id)['blocked']
    for path in engine.directory.rglob('*'):
        if path.is_file(): assert b'FAKE-MVP-KEY-NEVER-PERSIST' not in path.read_bytes()


def test_first_approval_review_and_adoption_are_separate(live_mvp):
    engine, fake = live_mvp
    run(engine)
    assert not fake.calls
    value = engine.image_preview()
    with pytest.raises(ValueError): engine.approve_images(value['token'])
    approve(engine)
    run(engine)
    assert len(fake.calls) == 1 and not engine.images.store.load()['adoptions']
    assert engine.image_preview() is None
    assert run(engine)['action'] is None and len(fake.calls) == 1
    candidate = engine.images.store.load()['candidates'][0]
    with pytest.raises(ValueError): engine.confirm_first_image(candidate['id'], confirmed=True, test_only=True)
    review(engine)
    assert not engine.images.store.load()['adoptions']
    engine.confirm_first_image(candidate['id'], confirmed=True, test_only=True)
    assert engine.image_preview()['count'] == 1


def test_timeout_stops_at_panel_holds_reservation_no_resend(live_mvp):
    engine, fake = live_mvp
    panels(engine)
    approve(engine)
    original = fake.__class__.__call__
    def fail_third_panel(self, model, body):
        if len(self.calls) == 4:
            self.error = TimeoutError('SECRET forbidden raw detail')
        return original(self, model, body)
    from unittest.mock import patch
    with patch.object(FakeImage, '__call__', fail_third_panel), pytest.raises(ValueError):
        run(engine)
    assert len(fake.calls) == 5
    assert 'SECRET' not in engine.images.store.path.read_text()
    from manga.image_ai_live_plan import shared_ledger
    assert shared_ledger(engine.images).summary(engine.project_id)['blocked']
    assert engine.image_preview() is None
    assert run(engine)['action'] is None and len(fake.calls) == 5
    assert len(engine.images.store.load()['candidates']) == 4


def test_batch_quote_budget_and_changed_source_prevent_send(live_mvp):
    engine, fake = live_mvp
    panels(engine)
    value = engine.image_preview()
    engine.story.artwork.save_style('変わった画風', expected_revision=engine.story.artwork.load()['revision'])
    with pytest.raises(StorageError): engine.approve_images(value['token'], confirmed=True)
    assert len(fake.calls) == 2


def test_high_quality_batch_exceeds_shared_project_budget(live_mvp):
    engine, fake = live_mvp
    panels(engine)
    price = card(thinking_level='high', max_output_tokens=4096, max_thinking_tokens=4096)
    engine.images.save_lite_configuration(price, expected_revision=engine.images.store.load()['revision'])
    engine.reinspect(engine.plan()['token'], confirmed=True)
    value = engine.image_preview()
    assert value['count'] == 24 and not value['possible']
    with pytest.raises(ValueError): engine.approve_images(value['token'], confirmed=True)
    assert len(fake.calls) == 2


def test_settled_image_save_failure_recovers_without_sending_again(live_mvp, monkeypatch):
    engine, fake = live_mvp
    run(engine)
    approve(engine)
    original = engine.images.store.write
    def fail(path, *args, **kwargs):
        if '/candidates/' in path:
            raise OSError('disk full')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(engine.images.store, 'write', fail)
    with pytest.raises(ValueError): run(engine)
    job = engine.images.store.load()['jobs'][0]
    assert job['state'] == 'artifact_pending' and len(fake.calls) == 1
    monkeypatch.setattr(engine.images.store, 'write', original)
    engine.recover_image_save(job['id'], confirmed=True)
    assert len(engine.images.store.load()['candidates']) == 1 and len(fake.calls) == 1
    assert run(engine)['action'] is None


def test_restart_mid_scope_reuses_completed_images_and_same_approval(live_mvp):
    engine, fake = live_mvp
    panels(engine)
    scope = approve(engine)
    run(engine, limit=2)
    ids = [c['id'] for c in engine.images.store.load()['candidates']]
    assert len(fake.calls) == 4
    reopened = MangaMVP(engine.directory, engine.project_id)
    reopened.images._live_adapter = engine.images.live_adapter
    run(reopened, limit=1)
    assert len(fake.calls) == 5
    assert [c['id'] for c in reopened.images.store.load()['candidates']][:4] == ids
    assert all(j['live']['scope_id'] == reopened.images.store.load()['jobs'][2]['live']['scope_id'] for j in reopened.images.store.load()['jobs'][2:])
    assert scope['count'] == 24


@pytest.mark.parametrize('cap', ['0', '501', 'NaN'])
def test_invalid_user_budget_is_not_a_new_budget_pool(tmp_path, cap):
    with pytest.raises(ValueError):
        creation_plan(tmp_path, dict(theme='作品', genre='青春', page_count=8, style='漫画'), str(uuid4()), image_config=card(), max_budget=cap)
