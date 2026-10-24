import io
import json
from pathlib import Path

import pytest
import streamlit as native
from streamlit.testing.v1 import AppTest

from manga.manual_image_ui import receipt
from manga.production_dashboard import dashboard
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from image_ai_fixtures import image_workspace
from manga.character_ai_samples import example_image
from ui_helpers import go, button, element

APP = Path(__file__).resolve().parents[1]/'app.py'


def open_app():
    app = AppTest.from_file(str(APP),default_timeout=60).run()
    button(app,'選択したプロジェクトを開く')
    return app


def test_dashboard_navigation_counts_and_manual_jump(image_workspace):
    project,service,_ = image_workspace
    app = open_app()
    frame = next(f.value for f in app.dataframe if '工程' in f.value.columns)
    assert len(frame) == 9 and frame.iloc[4]['状態'] != '完了'
    element(app,'selectbox','ダッシュボードから制作する未完成コマ').set_value('2:3').run()
    button(app,'この未完成コマを手動画像制作で開く')
    assert not app.exception and element(app,'selectbox','画像を作るページ・コマ').value == '2:3'
    button(app,'このコマの作画指示を編集')
    assert element(app,'selectbox','画像を確認するページ').value == 1
    assert element(app,'selectbox','画像を管理するコマ').value == 2


def test_manual_upload_rejects_mismatched_receipt_and_requires_explicit_adoption(image_workspace,monkeypatch):
    project,service,_ = image_workspace
    class Upload(io.BytesIO):
        def __init__(self,raw,name): super().__init__(raw); self.name = name
    uploads = {}
    monkeypatch.setattr(native,'file_uploader',lambda label,**kw:uploads.get(label))
    app = open_app(); go(app,'イラスト制作','手動画像制作')
    element(app,'selectbox','画像を作るページ・コマ').set_value('1:1').run()
    button(app,'この対象のプロンプトと基準画像を準備')
    assert not app.error
    assert element(app,'button','検証して画像候補を取り込む').disabled
    plan = service.plan('panel',page=1,panel=1)
    invalid = receipt(plan); invalid['panel'] = 2
    uploads['手動生成画像'] = Upload(example_image(),'manual.png')
    uploads['生成時の対象確認票（必須）'] = Upload(json.dumps(invalid).encode(),'ticket.json')
    app.run(); button(app,'検証して画像候補を取り込む')
    assert app.error and not service.store.load()['jobs']
    uploads['生成時の対象確認票（必須）'] = Upload(json.dumps(receipt(plan)).encode(),'ticket.json')
    app.run(); button(app,'検証して画像候補を取り込む')
    assert not app.error and len(service.store.load()['candidates']) == 1 and not service.store.load()['adoptions']
    for c in list(app.checkbox):
        if c.label.startswith('確認：'): c.check()
    app.run()
    element(app,'text_area','目視確認メモ・素材の出典（必須）').set_value('自作の検証画像、確認操作を模擬').run()
    button(app,'この候補の目視・権利確認を保存')
    element(app,'checkbox','この候補を正規登録・採用し、前の採用画像も履歴へ保持することを承認した').check().run()
    button(app,'この候補をArtworkへ登録・採用')
    assert not app.error and len(service.store.load()['adoptions']) == 1
    assert len(service.artwork.load()['panels']['1:1']['candidates']) == 2


def test_home_no_generation_and_final_confirmation_disabled(image_workspace,monkeypatch):
    _,service,_ = image_workspace
    def forbidden(*a,**kw): raise AssertionError('dashboard must not generate')
    monkeypatch.setattr(type(service),'create_job',forbidden)
    app = open_app()
    assert element(app,'button','完成品の最終確認を保存').disabled
    app.run()
    assert not app.exception and not service.store.load()['jobs']
    assert not service.store.path.exists()


def test_saved_page_output_reused_after_new_browser_session(image_workspace,monkeypatch):
    project,service,_ = image_workspace
    from manga.page_storage import PageStore
    pages = PageStore(service.directory,project.id)
    settings = pages.load()['settings']; settings['width'] = 600
    pages.save_settings(settings,expected_revision=0)
    app = open_app(); go(app,'完成・書き出し','PNG・PDF書き出し')
    element(app,'selectbox','出力形式').set_value('pdf').run()
    button(app,'出力ファイルを生成')
    assert not app.error
    from manga.studio_storage import StudioStore
    studio = StudioStore(service.directory,project.id)
    assert len(studio.load()['exports']) == 1
    def forbidden(*a,**kw): raise AssertionError('saved artifact should be reused')
    monkeypatch.setattr('manga.page_ui.export_book',forbidden)
    reopened = open_app(); go(reopened,'完成・書き出し','PNG・PDF書き出し')
    element(reopened,'selectbox','出力形式').set_value('pdf').run()
    button(reopened,'出力ファイルを生成')
    assert not reopened.exception and not reopened.error
    assert len(studio.load()['exports']) == 1
    assert any(d.label == '完成ファイルをダウンロード' for d in reopened.get('download_button'))


def test_next_action_opens_saved_image_job_recovery(image_workspace):
    _,service,_ = image_workspace
    service.create_job(service.plan('panel',page=1,panel=1),expected_revision=0)
    app = open_app()
    button(app,'次の工程へ進む')
    assert not app.exception
    assert element(app,'radio','画像制作の段階').value == '履歴・復旧'
    assert any(b.label == '選択した画像依頼の保存を再開' for b in app.button)
