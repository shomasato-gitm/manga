"""同一画面での作画保存から対象確認票付き画像の確認・別操作採用まで。"""
import copy
import io
import json
from pathlib import Path

import pytest
import streamlit as native
from streamlit.testing.v1 import AppTest

from image_ai_fixtures import image_workspace
from manga.artwork_storage import ArtworkStore
from manga.character_ai_samples import example_image
from manga.manual_image_ui import receipt
from manga.storage import StorageError, write_json_atomic
from ui_helpers import button, element, go

APP = Path(__file__).resolve().parents[1]/'app.py'


def opened():
    app = AppTest.from_file(str(APP),default_timeout=60).run()
    button(app,'選択したプロジェクトを開く')
    go(app,'イラスト制作','手動画像制作')
    return app


def missing(service,key='1:1'):
    data = service.artwork.load()
    data['panels'][key]['direction'] = None
    write_json_atomic(service.artwork.path,data)


def test_missing_direction_can_be_saved_inline_then_prompt_is_ready_and_survives_navigation(image_workspace):
    project, service, _ = image_workspace
    missing(service)
    original = copy.deepcopy(service.artwork.load())
    story_bytes = service.artwork.story_store.path.read_bytes()
    app = opened()
    assert any('作画指示が未保存' in i.value for i in app.info)
    assert element(app,'button','この対象のプロンプトと基準画像を準備').disabled
    assert any('STEP 2' in h.value for h in app.subheader)
    assert element(app,'multiselect','登場キャラクター').value
    element(app,'text_area','場所').set_value('確認した教室').run()
    button(app,'ホーム');go(app,'イラスト制作','手動画像制作')
    assert element(app,'text_area','場所').value == '確認した教室'
    button(app,'このコマの作画指示を保存')
    assert not app.error and service.artwork.load()['panels']['1:1']['direction']['location'] == '確認した教室'
    assert not element(app,'button','この対象のプロンプトと基準画像を準備').disabled
    button(app,'この対象のプロンプトと基準画像を準備')
    assert not app.error and any(d.label=='対象確認票をダウンロード' for d in app.get('download_button'))
    assert service.artwork.load()['panels']['1:1']['candidates'] == original['panels']['1:1']['candidates']
    assert service.artwork.load()['characters'] == original['characters']
    assert service.artwork.story_store.path.read_bytes() == story_bytes
    reopened = opened()
    element(reopened,'checkbox','作画指示を編集').check().run()
    assert element(reopened,'text_area','場所').value == '確認した教室'


def test_changed_script_requires_explicit_reconfirmation_and_keeps_old_input_and_images(image_workspace):
    _, service, _ = image_workspace
    app = opened()
    element(app,'checkbox','作画指示を編集').check().run()
    element(app,'text_area','場所').set_value('旧版の未保存入力').run()
    latest = service.store.stories.load_history()[-1]
    story = copy.deepcopy(latest['story'])
    story['pages'][0]['panels'][0]['description'] += '窓の外を見る。'
    service.store.stories.save(story,expected_version_id=latest['id'])
    before = service.artwork.path.read_bytes()
    app.run()
    assert any('以前の脚本版' in w.value for w in app.warning)
    assert any('以前の未保存入力' in w.value for w in app.warning)
    assert element(app,'text_area','場所').value != '旧版の未保存入力'
    assert element(app,'button','このコマの作画指示を保存').disabled
    assert element(app,'button','この対象のプロンプトと基準画像を準備').disabled
    assert service.artwork.path.read_bytes() == before
    element(app,'checkbox','現在の脚本と以前の作画指示・登場人物を照合し、保存し直すことを確認した').check().run()
    button(app,'このコマの作画指示を保存')
    assert service.artwork.load()['panels']['1:1']['direction']['story_version_id'] != latest['id']
    assert service.artwork.load()['panels']['1:1']['candidates']
    # 作画保存だけでは脚本確定やバイブルの再承認を行わない。
    assert element(app,'button','この対象のプロンプトと基準画像を準備').disabled


def test_batch_preview_and_selected_save_do_not_touch_existing_or_unselected(image_workspace):
    _, service, _ = image_workspace
    missing(service,'2:1');missing(service,'2:2')
    original = copy.deepcopy(service.artwork.load()['panels'])
    app = opened()
    button(app,'正式脚本から作画指示の下書きを一括準備')
    assert not app.error
    assert set(element(app,'multiselect','確認して保存するコマ').options) == {'2:1','2:2'}
    assert element(app,'button','選択したコマの作画指示を保存').disabled
    assert service.artwork.load()['panels'] == original
    element(app,'multiselect','確認して保存するコマ').set_value(['2:1']).run()
    element(app,'checkbox','選択した各コマの人物候補と脚本の対応・空欄の任意項目を確認した').check().run()
    button(app,'選択したコマの作画指示を保存')
    assert not app.error
    current = service.artwork.load()['panels']
    assert current['2:1']['direction'] and current['2:2']['direction'] is None
    assert current['1:1'] == original['1:1']


def test_save_failure_explains_recovery_and_preserves_input(image_workspace,monkeypatch):
    _, service, _ = image_workspace
    missing(service)
    app = opened()
    element(app,'text_area','場所').set_value('保存失敗時にも残す').run()
    before = service.artwork.path.read_bytes()
    def fail(*args,**kwargs):
        raise StorageError('disk full: technical detail')
    monkeypatch.setattr(ArtworkStore,'save_direction',fail)
    button(app,'このコマの作画指示を保存')
    assert app.error and any('保存先・空き容量' in e.value for e in app.error)
    assert all('technical detail' not in e.value for e in app.error)
    assert element(app,'text_area','場所').value == '保存失敗時にも残す'
    assert service.artwork.path.read_bytes() == before


def test_receipt_import_review_adopt_and_next_panel_are_separate_operations(image_workspace,monkeypatch):
    project, service, _ = image_workspace
    missing(service)
    class Upload(io.BytesIO):
        def __init__(self, raw, name):
            super().__init__(raw);self.name=name
    uploads = {}
    monkeypatch.setattr(native,'file_uploader',lambda label,**kwargs:uploads.get(label))
    app = opened();button(app,'このコマの作画指示を保存')
    button(app,'この対象のプロンプトと基準画像を準備')
    plan = service.plan('panel',page=1,panel=1)
    ticket = receipt(plan)
    bad = dict(ticket,panel=2)
    uploads['手動生成画像'] = Upload(example_image(),'TEST_ONLY_phase8.png')
    uploads['生成時の対象確認票（必須）'] = Upload(json.dumps(bad).encode(),'ticket.json')
    app.run();button(app,'検証して画像候補を取り込む')
    assert any('対象確認票がこのコマに一致しません' in e.value for e in app.error)
    assert not service.store.load()['jobs']
    uploads['生成時の対象確認票（必須）'] = Upload(json.dumps(ticket).encode(),'ticket.json')
    app.run();button(app,'検証して画像候補を取り込む')
    assert not app.error and len(service.store.load()['candidates']) == 1
    assert not service.store.load()['adoptions']
    assert element(app,'button','この候補をArtworkへ登録・採用').disabled
    for checkbox in app.checkbox:
        if checkbox.label.startswith('確認：'):
            checkbox.check()
    app.run()
    element(app,'text_area','目視確認メモ・素材の出典（必須）').set_value('独立したモック画像。確認操作のみ検証、公開品質ではない。').run()
    button(app,'この候補の目視・権利確認を保存')
    assert not service.store.load()['adoptions']
    element(app,'checkbox','この候補を正規登録・採用し、前の採用画像も履歴へ保持することを承認した').check().run()
    button(app,'この候補をArtworkへ登録・採用')
    assert not app.error and len(service.store.load()['adoptions']) == 1
    # 採用後に対象が勝手に切り替わらず、明示操作で次へ進む。
    assert element(app,'selectbox','画像を作るページ・コマ').value == '1:1'
    button(app,'次の未完成コマへ進む')
    assert element(app,'selectbox','画像を作るページ・コマ').value == '1:2'
