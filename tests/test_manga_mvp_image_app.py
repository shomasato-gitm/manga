from pathlib import Path
from uuid import uuid4

from streamlit.testing.v1 import AppTest

from gemini_image_fixtures import card
from manga.manga_mvp import MangaMVP, creation_plan, create
from ui_helpers import button, element

APP = Path(__file__).resolve().parents[1] / 'app.py'


def test_image_choice_requires_terms_and_keeps_text_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('MANGA_DATA_DIR', str(tmp_path / 'projects'))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    button(app, 'テーマから新しい漫画をまとめて制作')
    element(app, 'text_area', '漫画のテーマ').set_value('桜色のタイムカプセル').run()
    element(app, 'selectbox', '一括制作の画像AI').set_value('gemini').run()
    assert not app.exception
    assert not any(b.label == '漫画を制作' for b in app.button)
    assert element(app, 'selectbox', '画像品質・思考上限').value == 'minimal'
    assert any('無料枠はありません' in i.value for i in app.info)
    element(app, 'checkbox', '公式モデル・最大課金量・有料アカウントの利用権限・利用規約と、プロンプト／人物設定／参照画像の外部送信条件を確認した').check().run()
    assert element(app, 'button', '漫画を制作').disabled
    assert element(app, 'text_area', '漫画のテーマ').value == '桜色のタイムカプセル'
    assert any('417.3312' in m.value for m in app.markdown)


def test_first_image_requires_scope_and_production_flag_no_http(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No network')))
    directory = tmp_path / 'projects'
    monkeypatch.setenv('MANGA_DATA_DIR', str(directory))
    monkeypatch.setenv('MANGA_BUDGET_DB', str(tmp_path / 'budget.sqlite3'))
    monkeypatch.delenv('MANGA_ENABLE_GEMINI_LITE_IMAGE', raising=False)
    plan = creation_plan(directory, dict(theme='桜色のタイムカプセル', genre='青春', page_count=1, style='漫画'), str(uuid4()), image_config=card())
    project = create(directory, plan, approved=True)
    engine = MangaMVP(directory, project.id)
    engine.run(engine.plan()['token'], approved=True)
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    button(app, '選択したプロジェクトを開く')
    assert not app.exception and not app.error
    assert element(app, 'button', 'この範囲だけ画像生成を承認して進める').disabled
    assert any('OFF' in w.value for w in app.warning)
    assert not engine.images.store.load()['jobs']
    button(app, '制作状態を更新')
    assert not app.exception and not engine.images.store.load()['jobs']
