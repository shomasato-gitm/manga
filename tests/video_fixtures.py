"""APIと実ユーザーデータを使わない、8ページ24コマの動画素材。"""
import hashlib
import socket
import pytest

from scripts.verify_video import fixture


@pytest.fixture
def video_workspace(tmp_path,monkeypatch):
    monkeypatch.setenv('MANGA_DATA_DIR',str(tmp_path/'demo'/'data'/'projects'))
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'budget.sqlite3'))
    monkeypatch.delenv('MANGA_ENABLE_PAID_TTS',raising=False)
    def forbidden(*a,**k):raise AssertionError('Video must never call a paid API or external network')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    from manga.budget import BudgetedExecutor
    from manga.voice_polly import PollyVoice
    monkeypatch.setattr(BudgetedExecutor,'run',forbidden);monkeypatch.setattr(PollyVoice,'synthesize',forbidden)
    return fixture(tmp_path/'demo')


def protected(service):
    return {str(p.relative_to(service.store.root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in service.store.root.rglob('*') if p.is_file() and not p.is_symlink() and
        not p.relative_to(service.store.root).parts[0].startswith(('video','.video'))}


def create_job(service,timeline,kinds=None,force=False):
    return service.create_job(timeline['id'],kinds or ['youtube_main','tiktok_vertical','youtube_shorts'],
        expected_revision=service.store.load()['revision'],acknowledged=True,force=force)
