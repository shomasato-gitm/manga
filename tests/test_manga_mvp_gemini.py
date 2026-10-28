"""既存Gemini HTTP・共通予算を通すフェイク結合。実APIの利用権・品質を保証しない。"""
import io
import json
import socket
from uuid import uuid4

import pytest

from manga.manga_mvp import MangaMVP, create, creation_plan
from manga.budget.config import open_budget
from manga.story_ai_mock import MockStoryAI
from manga.storage import StorageError
from character_gemini_fixtures import answer
from test_gemini35 import card35, raw35


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('Real HTTP forbidden'))
    monkeypatch.setenv('GEMINI_API_KEY', 'FAKE-MVP-KEY-NEVER-PERSIST')
    monkeypatch.setenv('MANGA_ENABLE_TEXT_API', '1')
    monkeypatch.setenv('MANGA_BUDGET_DB', str(tmp_path / 'shared.sqlite3'))
    directory = tmp_path / 'projects'
    values = dict(theme='桜色のタイムカプセル', genre='青春', page_count=8, style='日本漫画')
    plan = creation_plan(directory, values, str(uuid4()), mode='verified-free', card=card35(free=True))
    return directory, values, plan


def fake_http(engine, monkeypatch, *, fail_at=None, mutate=None):
    calls = []
    class Reply(io.BytesIO):
        status = 200
        def read1(self, n):
            return self.read(n)
    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == 'POST' and timeout == 30
            assert request.full_url.endswith('/models/gemini-3.5-flash-lite:generateContent')
            assert request.get_header('X-goog-api-key') == 'FAKE-MVP-KEY-NEVER-PERSIST'
            calls.append(json.loads(request.data))
            if len(calls) == fail_at:
                raise TimeoutError('raw private error: FAKE-MVP-KEY-NEVER-PERSIST')
            char = engine.characters.proposals.load()['requests']
            if char:
                payload = answer(char[-1])
            else:
                source = engine.story.store.load()['requests'][-1]
                payload = json.loads(MockStoryAI().generate(source)['text'])
                if source['kind'] == 'draft':
                    for page in payload['story']['pages']:
                        for panel in page['panels']:
                            panel['description'] = panel['description'].replace('主人公', '咲').replace('友人', '春斗')
                            name = '咲' if panel['panel'] % 2 else '春斗'
                            panel['dialogue'] = f'{name}：「桜の下でまた会おう。」'
            raw = raw35(json.dumps(payload, ensure_ascii=False))
            if mutate:
                mutate(raw)
            return Reply(json.dumps(raw).encode())
    monkeypatch.setattr('manga.story_ai_gemini.build_opener', lambda *args: Opener())
    return calls


def run(engine):
    return engine.run(engine.plan()['token'], approved=True)


def test_three_scoped_calls_shared_budget_and_image_boundary(live, monkeypatch):
    directory, _, plan = live
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    calls = fake_http(engine, monkeypatch)
    result = run(engine)
    assert len(calls) == 3 and '画像API' in result['reason']
    assert len(engine.story.stories.load_history()) == 1
    assert len(engine.story.artwork.load()['characters']) == 2
    assert not engine.images.store.load()['jobs']
    assert not engine.studio.load()['exports']
    assert open_budget(directory).summary(project.id)['project_used_jpy'] > 0
    for _ in range(2):
        run(MangaMVP(directory, project.id))
    assert len(calls) == 3
    for file in directory.parent.rglob('*'):
        if file.is_file():
            assert b'FAKE-MVP-KEY-NEVER-PERSIST' not in file.read_bytes()


@pytest.mark.parametrize('fail_at', [1, 2, 3])
def test_timeout_holds_shared_reservation_and_never_resends(live, monkeypatch, fail_at):
    directory, _, plan = live
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    calls = fake_http(engine, monkeypatch, fail_at=fail_at)
    with pytest.raises(ValueError):
        run(engine)
    assert len(calls) == fail_at
    summary = open_budget(directory).summary(project.id)
    assert summary['blocked'] and summary['project_reserved_jpy'] > 0
    with pytest.raises(ValueError):
        run(MangaMVP(directory, project.id))
    assert len(calls) == fail_at
    assert 'FAKE-MVP-KEY-NEVER-PERSIST' not in str(engine.load())


@pytest.mark.parametrize('mutate', [lambda r:r.update(modelVersion='different-model'), lambda r:r['candidates'][0]['content']['parts'][0].update(text='{bad JSON')])
def test_invalid_response_or_model_never_adopts_or_retries(live, monkeypatch, mutate):
    directory, _, plan = live
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    calls = fake_http(engine, monkeypatch, mutate=mutate)
    with pytest.raises(ValueError):
        run(engine)
    assert not engine.story.stories.load_history()
    with pytest.raises(ValueError):
        run(engine)
    assert len(calls) == 1


@pytest.mark.parametrize('limit', ['project', 'month', 'uncertain'])
def test_creation_checks_all_shared_limits(live, limit):
    directory, _, plan = live
    ledger = open_budget(directory)
    target = plan['project_id'] if limit == 'project' else str(uuid4())
    request = str(uuid4())
    ledger.reserve(project_id=target, request_id=request, provider='test', model='fake', kind='test', max_jpy='499' if limit != 'month' else '500')
    if limit != 'uncertain':
        ledger.settle(request, actual_jpy='499' if limit == 'project' else '500')
    else:
        ledger.mark_uncertain(request)
    if limit == 'month':
        for _ in range(9):
            identifier = str(uuid4())
            ledger.reserve(project_id=str(uuid4()), request_id=identifier, provider='test', model='fake', kind='test', max_jpy='500')
            ledger.settle(identifier, actual_jpy='500')
    with pytest.raises(ValueError, match='予算|請求'):
        create(directory, plan, approved=True)
    assert not (directory / (plan['project_id'] + '.json')).exists()


@pytest.mark.parametrize('fault', ['free', 'model', 'flag', 'cap'])
def test_unverified_configuration_is_not_executable(live, monkeypatch, fault):
    directory, values, _ = live
    card = card35(free=True)
    if fault == 'free':
        card['free_tier_reviewed'] = False
    elif fault == 'model':
        card['model'] = 'unverified-model'
    elif fault == 'flag':
        monkeypatch.delenv('MANGA_ENABLE_TEXT_API')
    else:
        card['fx_jpy_per_usd'] = '10000'
    with pytest.raises(ValueError):
        creation_plan(directory, values, str(uuid4()), mode='verified-free', card=card)


def test_missing_key_never_sends_and_changed_settings_or_ledger_stop(live, monkeypatch):
    directory, _, plan = live
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    calls = fake_http(engine, monkeypatch)
    monkeypatch.delenv('GEMINI_API_KEY')
    with pytest.raises(ValueError):
        run(engine)
    assert not calls
    monkeypatch.setenv('MANGA_BUDGET_DB', str(directory.parent / 'different.sqlite3'))
    with pytest.raises(StorageError):
        engine.api_consent()
