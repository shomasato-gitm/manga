import json

import pytest

from manga.character_ai_generation import text_store
from manga.budget.config import open_budget
from character_gemini_fixtures import character_gemini,answer,SECRET
from test_character_ai_app import open_studio,element,view
from test_gemini35 import raw35
from ui_helpers import button,go


def select_gemini(app):
    element(app,'radio','人物設定案の生成方法').set_value('Gemini 3.5 Flash-Lite').run(timeout=60)


def install_fake(service,monkeypatch,*,fail=False):
    from manga import story_ai_gemini as module
    from manga.story_ai_gemini import GeminiTransportError
    calls=[]
    def send(body,key):
        assert key==SECRET;calls.append(body)
        if fail:raise GeminiTransportError('bad_request_api_key')
        return raw35(json.dumps(answer(service.proposals.load()['requests'][-1])))
    monkeypatch.setattr(module,'transport',send)
    return calls


def authorize_and_generate(app):
    element(app,'checkbox','人物生成の共有予算残額を確認する').check().run(timeout=60)
    element(app,'checkbox','この人物設定の1送信だけ、モデル・送信内容・利用条件・最大費用を確認して承認する').check().run(timeout=60)
    button(app,'Gemini 3.5で人物設定案を1回生成')


def test_existing_character_screen_gemini_edit_partial_adoption_bible_flow(character_gemini,monkeypatch):
    project,service=character_gemini;calls=install_fake(service,monkeypatch);before=service.artwork.path.read_bytes();person=service.artwork.load()['characters'][0]
    app=open_studio()
    assert element(app,'radio','人物設定案の生成方法').value=='無料モック' and not calls
    select_gemini(app)
    assert not app.exception and not app.error and not calls
    assert element(app,'button','Gemini 3.5で人物設定案を1回生成').disabled
    assert any('73.328640' in m.value for m in app.markdown)
    assert any('STEP 2' in h.value for h in app.subheader) and any('STEP 3' in h.value for h in app.subheader)
    authorize_and_generate(app)
    assert not app.exception and not app.error and len(calls)==1 and service.artwork.path.read_bytes()==before
    assert element(app,'button','Gemini 3.5で人物設定案を1回生成').disabled
    button(app,'STEP 4：人物候補の修正・採用へ')
    assert element(app,'selectbox','同定した反映先（自動選択しません）').value is None
    assert element(app,'button','この人物の選択項目だけを正式採用').disabled
    element(app,'selectbox','同定した反映先（自動選択しません）').select(person['id']).run(timeout=60)
    element(app,'multiselect','採用する項目（選択外の既存値は保持）').set_value(['personality','relationships']).run(timeout=60)
    element(app,'text_area','採用値：性格').set_value('人間が修正した性格').run(timeout=60)
    assert any('未保存' in c.value for c in app.caption)
    button(app,'修正案だけを保存（正式採用しない）')
    assert not app.error and service.artwork.path.read_bytes()==before and len(calls)==1
    proposal=service.proposals.load()['proposals'][0]
    assert proposal['payload']['items'][0]['fields']['personality']=='人間が修正した性格'
    assert proposal['original_payload']['items'][0]['fields']['personality']!='人間が修正した性格'
    element(app,'checkbox','人物の同定・原文・提案と事実の区別・選択項目を確認した').check().run(timeout=60)
    button(app,'この人物の選択項目だけを正式採用')
    assert not app.error and not app.exception and len(calls)==1
    current=service.artwork.load()['characters'][0]
    assert current['id']==person['id'] and current['references']==person['references'] and current['hair_color']==person['hair_color']
    button(app,'STEP 5：採用人物のバイブルを確認する')
    assert element(app,'selectbox','バイブルを確認する人物').value==person['id']
    element(app,'text_area','今回の確認・採用根拠（必須）').set_value('人物の同定と提案の区別を確認').run(timeout=60)
    element(app,'checkbox','固定設定・別名・参照状態・場面例外を確認した').check().run(timeout=60)
    button(app,'確認済みバイブルを新しい版として保存')
    assert not app.error and service.store.load()['bibles'][0]['profile']['relationships']==current['relationships']
    assert len(calls)==1 and SECRET not in str(app)


def test_gemini_flags_missing_card_and_drafts_never_send_implicitly(character_gemini,monkeypatch):
    project,service=character_gemini;calls=install_fake(service,monkeypatch);monkeypatch.delenv('MANGA_ENABLE_TEXT_API')
    app=open_studio();select_gemini(app)
    element(app,'checkbox','人物生成の共有予算残額を確認する').check().run()
    assert element(app,'button','Gemini 3.5で人物設定案を1回生成').disabled
    element(app,'text_area','追加して確認する人物名（任意・1行1名、脚本に記述が必要）').set_value('春斗').run()
    go(app,'企画・ストーリー','ホーム・進行状況');go(app,'キャラクター・作風','AIキャラクター制作・一貫性管理')
    assert element(app,'radio','人物設定案の生成方法').value=='Gemini 3.5 Flash-Lite'
    assert element(app,'text_area','追加して確認する人物名（任意・1行1名、脚本に記述が必要）').value=='春斗'
    assert not calls and not app.exception and not service.proposals.load()['requests']
    store=text_store(service);data=store.load();data['card']=None;store.save(data,'test_remove_card')
    app.run(timeout=60)
    assert any('共通料金' in i.value for i in app.info)
    button(app,'既存のGemini接続・料金設定を開く')
    assert element(app,'radio','AIストーリーの制作段階').value=='制作条件' and not calls


def test_http_failure_shows_bill_pending_and_returns_from_common_budget(character_gemini,monkeypatch):
    project,service=character_gemini;calls=install_fake(service,monkeypatch,fail=True);app=open_studio();select_gemini(app)
    authorize_and_generate(app)
    assert len(calls)==1 and not app.exception and app.error
    assert any('400' in e.value and 'APIキー' in e.value for e in app.error)
    assert any('通信・請求の確認待ち' in m.value for m in app.markdown)
    assert element(app,'button','Gemini 3.5で人物設定案を1回生成').disabled
    button(app,'人物生成の請求明細・手動照合へ')
    assert any('請求未確定' in h.value for h in app.subheader)
    button(app,'AIキャラクターの保存・請求照合へ戻る')
    assert element(app,'radio','人物設定案の生成方法').value=='Gemini 3.5 Flash-Lite'
    button(app,'人物生成の保存を再開・請求を照合（再送なし）')
    assert any('未確定' in e.value for e in app.error) and len(calls)==1
    assert open_budget(service.store.root.parent).summary(project.id)['blocked'] and SECRET not in str(app)


def test_unfinalized_source_disables_gemini_and_mock(character_gemini,monkeypatch):
    _,service=character_gemini;calls=install_fake(service,monkeypatch);latest=service.proposals.latest()
    service.stories.save(latest['story'],expected_version_id=latest['id']);app=open_studio()
    assert element(app,'button','無料モックで人物候補・設定案を生成').disabled
    select_gemini(app)
    assert not any(b.label=='Gemini 3.5で人物設定案を1回生成' and not b.disabled for b in app.button)
    assert any('未確定' in w.value for w in app.warning) and not calls and not app.exception
