import io
import json

import streamlit as native

from manga.image_ai_live_samples import fake_card
from manga.image_ai_live_provider import GeminiImageAdapter
from manga.image_ai_service import ImageAIService
from image_ai_fixtures import image_workspace
from test_image_ai_live import setup_live
from test_image_ai_app import open_studio,view,panel_plan
from ui_helpers import button,element


def test_live_ui_is_off_even_after_flag_key_card_and_consent(image_workspace,monkeypatch):
    monkeypatch.setenv('MANGA_ENABLE_IMAGE_API','1'); monkeypatch.setenv('GEMINI_API_KEY','SENTINEL_PRIVATE_TEST_VALUE')
    uploads={}
    monkeypatch.setattr(native,'file_uploader',lambda label,**kw:uploads.get(label))
    app=open_studio(); panel_plan(app); view(app,'生成・手動取り込み')
    element(app,'radio','画像候補の作り方').set_value('paid-locked').run(timeout=60)
    assert not element(app,'checkbox','実APIを使用する意図と外部送信を確認する（これだけでは有効になりません）').value
    element(app,'checkbox','実APIを使用する意図と外部送信を確認する（これだけでは有効になりません）').check().run(timeout=60)
    uploads['実APIの確認済み料金カード（JSONファイル・秘密情報を含めない）']=io.BytesIO(json.dumps(fake_card()).encode())
    app.run(timeout=60)
    for label in ('この1枚の最大費用を検証','承認した実API依頼を保存（まだ送信しない）','この1枚を生成'):
        assert element(app,'button',label).disabled
    button(app,'共有予算の支出・予約・残額を確認')
    assert not app.exception and not image_workspace[1].store.path.exists()
    assert all('SENTINEL_PRIVATE_TEST_VALUE' not in str(e.value) for e in app.markdown)


def test_fake_ui_two_stage_create_generate_then_review_separate(image_workspace,monkeypatch):
    values=setup_live(image_workspace); service,_,_,_,http=values
    # UIにfakeの選択肢は提供しない。テスト境界だけで同一サービスに注入。
    monkeypatch.setattr(ImageAIService,'live_adapter',property(lambda self:GeminiImageAdapter(fake_transport=http)))
    uploads={}; monkeypatch.setattr(native,'file_uploader',lambda label,**kw:uploads.get(label))
    app=open_studio(); panel_plan(app); view(app,'生成・手動取り込み')
    element(app,'radio','画像候補の作り方').set_value('paid-locked').run(timeout=60)
    uploads['実APIの確認済み料金カード（JSONファイル・秘密情報を含めない）']=io.BytesIO(json.dumps(fake_card()).encode())
    element(app,'checkbox','実APIを使用する意図と外部送信を確認する（これだけでは有効になりません）').check().run(timeout=60)
    for c in list(app.checkbox):
        if c.label.startswith('この参照画像の外部送信に同意：'): c.check()
    app.run(timeout=60); button(app,'この1枚の最大費用を検証')
    assert not http.calls
    element(app,'checkbox','上記最大費用と参照画像を確認し、この1枚の依頼保存を承認する').check().run(timeout=60)
    button(app,'承認した実API依頼を保存（まだ送信しない）')
    assert len(service.store.load()['jobs'])==1 and not http.calls
    assert element(app,'button','この1枚を生成').disabled
    element(app,'checkbox','保存した依頼ID・費用を再確認し、この1枚だけの送信を承認する').check().run(timeout=60)
    button(app,'この1枚を生成')
    assert len(http.calls)==1 and not app.exception and not service.store.load()['reviews'] and not service.store.load()['adoptions']
    app.run(timeout=60); assert len(http.calls)==1 and element(app,'button','この1枚を生成').disabled
    view(app,'候補比較・採用')
    assert element(app,'button','この候補をArtworkへ登録・採用').disabled
    view(app,'履歴・復旧')
    assert any('fake API' in c.value for c in app.caption)
    assert len(http.calls)==1 and not app.exception


def test_cli_zero_paid_calls_and_no_overwrite(tmp_path):
    from scripts.verify_image_ai_live_fake import verify
    output=tmp_path/'fresh'; report=verify(output)
    assert report['actual_api_cost_jpy']=='0' and report['real_api_calls']==0 and report['fake_http_calls']==1
    assert report['existing_files_preserved'] and len(report['image_sha256'])==64
    import pytest
    before=(output/'report.json').read_bytes()
    with pytest.raises(FileExistsError): verify(output)
    assert (output/'report.json').read_bytes()==before
