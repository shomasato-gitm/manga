from pathlib import Path
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from manga.manga_mvp import MangaMVP, create, creation_plan
from manga.image_ai_models import REVIEW_FIELDS
from ui_helpers import button, element, go

APP = Path(__file__).resolve().parents[1] / 'app.py'


def test_home_input_consent_new_work_and_resume_without_duplicate(tmp_path, monkeypatch):
    directory = tmp_path / 'projects'
    monkeypatch.setenv('MANGA_DATA_DIR', str(directory))
    monkeypatch.setattr(MangaMVP, 'start', lambda self, token:self.run(token, approved=True, limit=1))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    button(app, 'テーマから新しい漫画をまとめて制作')
    element(app, 'text_area', '漫画のテーマ').set_value('桜色のタイムカプセル').run()
    assert element(app, 'selectbox', '一括制作のテキストAI').value == 'mock'
    assert element(app, 'button', '漫画を制作').disabled
    consent = next(c for c in app.checkbox if c.label.startswith('上記の外部送信'))
    consent.check().run()
    button(app, '漫画を制作')
    assert not app.error and not app.exception
    project = app.session_state['active_project']
    engine = MangaMVP(directory, project.id)
    assert len(engine.story.store.load()['concept_batches']) == 1
    go(app, '漫画ページ編集')
    go(app, 'ホーム・進行状況')
    assert not app.exception and not app.error
    assert len(engine.story.store.load()['concept_batches']) == 1
    assert any('無料モック' in w.value for w in app.warning)
    button(app, '確認済みの範囲で制作を再開')
    assert len(engine.story.store.load()['concept_batches']) == 1


def test_home_review_is_separate_from_adoption_and_errors_are_visible(tmp_path, monkeypatch):
    directory = tmp_path / 'projects'
    monkeypatch.setenv('MANGA_DATA_DIR', str(directory))
    plan = creation_plan(directory, dict(theme='確認用作品', genre='青春', page_count=1, style='漫画'), str(uuid4()))
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    engine.run(engine.plan()['token'], approved=True)
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    button(app, '選択したプロジェクトを開く')
    assert element(app, 'button', '確認済みの範囲で制作を再開').disabled
    for label in REVIEW_FIELDS.values():
        element(app, 'checkbox', label).check()
    element(app, 'text_area', '画像の確認メモ・権利の根拠').set_value('無料モックのテスト採用。公開不可。').run()
    button(app, '全対象の画像確認を記録（まだ採用しない）')
    assert not engine.images.store.load()['adoptions']
    assert not element(app, 'button', '確認済みの範囲で制作を再開').disabled
    engine.story.artwork.save_style('変更後の画風', expected_revision=engine.story.artwork.load()['revision'])
    button(app, '制作状態を更新')
    assert any('素材が変更' in w.value for w in app.warning)
    assert element(app, 'button', '変更した素材を検査して再開を準備')


def test_gemini_without_shared_card_cannot_start_and_input_survives(tmp_path, monkeypatch):
    monkeypatch.setenv('MANGA_DATA_DIR', str(tmp_path / 'projects'))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    button(app, 'テーマから新しい漫画をまとめて制作')
    element(app, 'text_area', '漫画のテーマ').set_value('未送信のテーマ').run()
    element(app, 'selectbox', '一括制作のテキストAI').set_value('verified-free').run()
    assert not any(b.label == '漫画を制作' for b in app.button)
    assert any('料金JSON' in c.value for c in app.caption)
    element(app, 'selectbox', '一括制作のテキストAI').set_value('mock').run()
    assert element(app, 'text_area', '漫画のテーマ').value == '未送信のテーマ'
