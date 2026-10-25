from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from image_ai_fixtures import image_workspace
from ui_helpers import element,button,go
from manga.ux import PRIMARY,group

APP=Path(__file__).resolve().parents[1]/'app.py'


def opened():
    app=AppTest.from_file(str(APP),default_timeout=60).run()
    button(app,'選択したプロジェクトを開く')
    return app


@pytest.mark.parametrize('label',list(PRIMARY))
def test_major_routes_are_one_click_and_keep_legacy_access(image_workspace,label):
    app=opened();button(app,label)
    assert not app.exception
    assert element(app,'radio','表示モード').value=='標準モード'
    assert len(element(app,'radio','制作メニュー').options)==6
    menu,section=PRIMARY[label]
    assert element(app,'radio','制作メニュー').value==menu
    if section: assert element(app,'selectbox','作業を選択').value==section


def test_character_card_edits_correct_person_and_drafts_survive_mode(image_workspace):
    project,service,_=image_workspace
    app=opened();button(app,'キャラクター')
    people=[c for c in service.artwork.load()['characters'] if not c['deleted_at']]
    app.button(key='ux_person_'+people[1]['id']+'_epoch_0').click().run()
    assert element(app,'text_input','名前').value==people[1]['name']
    element(app,'text_input','名前').set_value('編集中の名前').run()
    element(app,'radio','表示モード').set_value('詳細モード').run()
    assert element(app,'text_input','名前').value=='編集中の名前'
    button(app,'ホーム');button(app,'キャラクター')
    app.button(key='ux_person_'+people[1]['id']+'_epoch_0').click().run()
    assert element(app,'text_input','名前').value=='編集中の名前'
    assert service.artwork.load()['characters'][1]['name']==people[1]['name']


def test_grid_card_opens_exact_manual_target_without_adopting(image_workspace):
    p,s,_=image_workspace
    app=opened();button(app,'画像制作')
    assert any('コマ' in c.value for c in app.caption)
    app.button(key='ux_panel_2:3_epoch_0').click().run()
    assert not app.exception
    assert element(app,'selectbox','画像を作るページ・コマ').value=='2:3'
    assert not s.store.load()['adoptions']


def test_audio_wizard_preserves_existing_inputs_and_safety(image_workspace):
    app=opened();button(app,'音声・動画')
    button(app,'1. 音声と字幕を開く')
    button(app,'2. 音声生成・試聴')
    assert not app.exception
    assert element(app,'radio','音声制作の作業').value=='音声生成・試聴'
    assert any('有料TTSは無効' in i.value for i in app.info)


def test_home_resume_progress_and_new_project_separation(image_workspace):
    p,s,_=image_workspace
    app=opened()
    assert element(app,'button','制作を再開する')
    assert any(m.label=='未完成のコマ' for m in app.metric)
    button(app,'画像制作');button(app,'ホーム');button(app,'前回の作業を開く')
    assert element(app,'selectbox','作業を選択').value=='ページ・コマ一覧'
    button(app,'新規プロジェクト')
    assert element(app,'text_input','タイトル').value==''


from story_ai_fixtures import ai_workspace,concepts


def test_concept_card_selects_and_displays_same_idea_without_adopting(ai_workspace):
    service=ai_workspace;concepts(service)
    before=service.stories.path.read_bytes()
    app=opened();button(app,'企画・脚本')
    element(app,'radio','AIストーリーの制作段階').set_value('企画案の生成・比較').run()
    idea=service.store.load()['concept_batches'][-1]['ideas'][2]
    app.button(key='story_ai_'+service.project_id+'_choose_'+idea['id']+'_epoch_0').click().run()
    assert not app.exception and not app.error
    assert service.store.load()['selections'][-1]['concept_id']==idea['id']
    assert any(idea['title'] in h.value for h in app.subheader)
    assert service.stories.path.read_bytes()==before


def test_complete_progress_card_uses_float_percent(monkeypatch):
    from types import SimpleNamespace
    import manga.ux as ux
    values=[]
    monkeypatch.setattr(ux,'st',SimpleNamespace(markdown=lambda *a,**kw:None,progress=lambda value,**kw:values.append(value)))
    ux.progress_card('完了した工程',1,1,'完了')
    assert values==[1.0] and type(values[0]) is float
