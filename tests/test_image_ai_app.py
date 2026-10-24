import io
from pathlib import Path

import streamlit as native
from PIL import Image
from streamlit.testing.v1 import AppTest

from manga.image_ai_service import ImageAIService
from image_ai_fixtures import image_workspace,generate
from ui_helpers import go,button,element

APP=Path(__file__).resolve().parents[1]/'app.py'


def open_studio():
    app=AppTest.from_file(str(APP),default_timeout=60).run()
    button(app,'選択したプロジェクトを開く'); go(app,'イラスト制作','AI画像生成・採用')
    assert not app.exception
    return app


def view(app,value):
    element(app,'radio','画像制作の段階').set_value(value).run(timeout=60)
    assert not app.exception


def panel_plan(app):
    element(app,'radio','計画の画像種類').set_value('panel').run(timeout=60)
    button(app,'この対象の生成計画を作成')
    assert not app.error


def test_plan_mock_preview_review_adopt_and_existing_workspace(image_workspace):
    _,service,_=image_workspace
    before=service.artwork.path.read_bytes(); app=open_studio()
    assert len(element(app,'radio','制作メニュー').options)==6
    panel_plan(app); view(app,'生成・手動取り込み')
    assert element(app,'radio','画像候補の作り方').value=='mock'
    button(app,'候補作成の依頼を保存（正規画像は変更しない）')
    assert not service.store.load()['candidates']
    button(app,'選択した依頼から1候補を作成')
    assert service.artwork.path.read_bytes()==before and len(service.store.load()['candidates'])==1
    view(app,'候補比較・採用')
    assert any('AI生成ではないテスト用画像' in w.value for w in app.warning)
    for c in list(app.checkbox):
        if c.label.startswith('確認：'): c.check()
    app.run(timeout=60)
    element(app,'text_area','目視確認メモ・素材の出典（必須）').set_value('専用のテスト画像として確認').run(timeout=60)
    button(app,'この候補の目視・権利確認を保存')
    element(app,'checkbox','この候補を正規登録・採用し、前の採用画像も履歴へ保持することを承認した').check().run(timeout=60)
    button(app,'この候補をArtworkへ登録・採用')
    assert not app.error and len(service.store.load()['adoptions'])==1
    selected=service.artwork.load()['panels']['1:1']['selected_image_id']
    assert selected==service.store.load()['adoptions'][0]['image_id']
    button(app,'採用したコマの既存制作画面へ')
    assert not app.exception and element(app,'radio','制作メニュー').value=='イラスト制作'


def test_manual_file_input_uses_same_validation_and_preserves_original(image_workspace,monkeypatch):
    _,service,_=image_workspace
    class Upload(io.BytesIO):
        name='ユーザー持込.png'
    stream=io.BytesIO(); Image.new('RGB',(64,96),'cyan').save(stream,'PNG'); raw=stream.getvalue()
    uploads={}
    monkeypatch.setattr(native,'file_uploader',lambda label,**kw:uploads.get(label))
    app=open_studio(); panel_plan(app); view(app,'生成・手動取り込み')
    element(app,'radio','画像候補の作り方').set_value('manual').run(timeout=60)
    assert element(app,'button','候補作成の依頼を保存（正規画像は変更しない）').disabled
    uploads['手動画像ファイル（PNG・JPEG・WebP、20MBまで）']=Upload(raw)
    app.run(timeout=60); button(app,'候補作成の依頼を保存（正規画像は変更しない）'); button(app,'選択した依頼から1候補を作成')
    assert not app.error and service.store.load()['candidates'][0]['provider']=='manual'
    job=service.store.load()['jobs'][0]
    assert service.store.read(f"image_ai/jobs/{job['id']}/input.bin")==raw and not service.store.load()['adoptions']


def test_paid_option_is_disabled_and_model_comparison_never_sends(image_workspace):
    _,service,_=image_workspace
    app=open_studio(); panel_plan(app); view(app,'生成・手動取り込み')
    element(app,'radio','画像候補の作り方').set_value('paid-locked').run(timeout=60)
    assert element(app,'button','候補作成の依頼を保存（正規画像は変更しない）').disabled
    assert not service.store.path.exists()
    view(app,'モデル・見積')
    assert any('未対応' in e.value for e in app.error) and not app.exception
    assert not service.store.path.exists()


def test_non_visible_screen_does_not_plan_generate_or_read_candidates(image_workspace,monkeypatch):
    def forbidden(*a,**k): raise AssertionError('Hidden screen should not perform image work')
    monkeypatch.setattr(ImageAIService,'plan',forbidden); monkeypatch.setattr(ImageAIService,'run',forbidden); monkeypatch.setattr(ImageAIService,'candidate_bytes',forbidden)
    app=open_studio()
    element(app,'text_area','構図の補足').set_value('人物を中央より右に置く').run(timeout=60)
    go(app,'企画・ストーリー','ホーム・進行状況'); go(app,'イラスト制作','AI画像生成・採用')
    assert element(app,'text_area','構図の補足').value=='人物を中央より右に置く' and not app.exception


def test_stale_candidate_disables_adoption_and_existing_files_remain(image_workspace):
    _,service,_=image_workspace; generate(image_workspace)
    service.artwork.save_style('フルカラーの別画風',expected_revision=service.artwork.load()['revision'])
    before=service.artwork.path.read_bytes()
    app=open_studio(); view(app,'候補比較・採用')
    assert any('要再確認' in w.value for w in app.warning)
    assert element(app,'button','この候補をArtworkへ登録・採用').disabled and service.artwork.path.read_bytes()==before


def test_unfinalized_script_cannot_create_plan(image_workspace):
    _,service,characters=image_workspace
    old=characters.proposals.latest(); characters.stories.save(old['story'],expected_version_id=old['id'])
    before=service.artwork.path.read_bytes(); app=open_studio(); panel_plan_error=False
    button(app,'この対象の生成計画を作成')
    assert app.error and not app.exception and not service.store.path.exists() and service.artwork.path.read_bytes()==before
