from image_ai_fixtures import image_workspace,hashes
from test_image_ai_app import open_studio,view,panel_plan
from ui_helpers import element


def test_readiness_display_and_no_go_does_not_modify_project(image_workspace,monkeypatch):
    _,service,_=image_workspace
    monkeypatch.setenv('GEMINI_API_KEY','SENTINEL_PRIVATE_VALUE'); monkeypatch.setenv('MANGA_ENABLE_IMAGE_API','1')
    before=hashes(service.store.root)
    app=open_studio(); view(app,'モデル・見積')
    assert any('本番接続の準備状況' in e.value for e in app.subheader)
    assert any('最大総課金額' in e.value for e in app.error)
    assert not app.exception and hashes(service.store.root)==before
    assert all('SENTINEL_PRIVATE_VALUE' not in str(e.value) for e in app.markdown)
    view(app,'生成計画'); panel_plan(app); view(app,'生成・手動取り込み')
    element(app,'radio','画像候補の作り方').set_value('paid-locked').run(timeout=60)
    assert element(app,'button','この1枚を生成').disabled
    assert element(app,'button','この1枚の最大費用を検証').disabled
    assert not service.store.path.exists() and hashes(service.store.root)==before
