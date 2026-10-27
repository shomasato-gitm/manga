"""3.5の設定・無料枠本人確認・診断・企画/脚本を実UIへ接続する。全HTTPはfake。"""
import json

from manga.gemini_models import DEFAULT_MODEL,LEGACY_MODEL
from manga.gemini_diagnostics import diagnostic_history
from manga.story_ai_service import StoryAIService
from manga.story_ai_mock import MockStoryAI
from manga.budget.config import open_budget
from story_ai_fixtures import ai_workspace
from test_gemini35 import card35,raw35,SECRET
from test_gemini_diagnostics_app import paste
from test_story_ai_app import open_studio,view
from test_story_ai_gemini import card as old_card
from ui_helpers import button


def test_new_ui_defaults_to_35_but_model_selection_does_not_save_or_call(ai_workspace,monkeypatch):
    service=ai_workspace;before=service.store.path.read_bytes();calls=[]
    import manga.story_ai_gemini as module
    monkeypatch.setattr(module,'transport',lambda *a:calls.append(a))
    app=open_studio()
    selector=next(s for s in app.selectbox if s.label=='Geminiモデル（設定・診断）')
    assert selector.value==DEFAULT_MODEL
    assert any('思考OFFではなく' in i.value for i in app.info)
    selector.set_value(LEGACY_MODEL).run();selector.set_value(DEFAULT_MODEL).run()
    assert not calls and service.store.path.read_bytes()==before and not app.exception


def test_saved_legacy_model_is_retained_and_switch_preserves_draft(ai_workspace):
    service=ai_workspace;service.configure(service.store.load()['settings'],old_card(),expected_revision=service.store.load()['revision'])
    before=service.store.path.read_bytes();app=open_studio()
    assert next(s for s in app.selectbox if s.label=='Geminiモデル（設定・診断）').value==LEGACY_MODEL
    paste(app,old_card())
    next(s for s in app.selectbox if s.label=='Geminiモデル（設定・診断）').set_value(DEFAULT_MODEL).run()
    next(c for c in app.checkbox if c.label=='公式モデル・アカウント利用権限・送信範囲・料金を確認した').check().run()
    button(app,'Gemini利用条件・料金を保存')
    assert app.error and service.store.path.read_bytes()==before and not app.exception
    assert json.loads(next(t for t in app.text_area if t.label=='Gemini利用条件・料金JSONを貼り付け').value)['model']==LEGACY_MODEL


def test_free_35_probe_needs_individual_consent_and_does_not_confirm_access(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as module
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1');calls=[]
    def fake(body,key):calls.append(body);return raw35()
    monkeypatch.setattr(module,'transport',fake)
    app=open_studio();paste(app,card35(access=False,free=True))
    next(s for s in app.selectbox if s.label=='初回診断の利用モード').set_value('verified-free').run()
    next(c for c in app.checkbox if c.label=='共有予算の残額を確認して実通信の準備をする').check().run()
    assert not calls and next(b for b in app.button if b.label=='承認した初回接続診断を1回だけ実行').disabled
    assert any('65.012640' in m.value and DEFAULT_MODEL in m.value for m in app.markdown)
    next(c for c in app.checkbox if c.label=='料金・利用条件・送信内容・最大費用を確認し、この固定文の診断を1回だけ承認する').check().run()
    button(app,'承認した初回接続診断を1回だけ実行')
    assert not app.exception and not app.error and len(calls)==1
    record=diagnostic_history(service)[-1]
    assert record['model']==DEFAULT_MODEL and record['state']=='success' and service.store.load()['card'] is None
    assert open_budget(service.directory).request_status(record['diagnosis_id'])['actual_jpy']>0
    assert calls[0]['generationConfig']['thinkingConfig']=={'thinkingLevel':'minimal'}
    assert SECRET not in str(app)


def test_free_35_concepts_and_story_candidates_use_existing_ui_and_human_stops(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as module
    service=ai_workspace;monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1');calls=[]
    monkeypatch.delenv('MANGA_ENABLE_PAID_STORY',raising=False)
    def fake(body,key):
        calls.append(body);request=service.store.load()['requests'][-1]
        return raw35(MockStoryAI().generate(request)['text'])
    monkeypatch.setattr(module,'transport',fake)
    monkeypatch.setattr(StoryAIService,'start',lambda self,identifier:self.run_job(identifier))
    original=service.stories.path.read_bytes();app=open_studio()
    next(s for s in app.selectbox if s.label=='企画・脚本の生成方法').set_value('verified-free').run()
    paste(app,card35(free=True))
    next(c for c in app.checkbox if c.label=='公式モデル・アカウント利用権限・送信範囲・料金を確認した').check().run()
    button(app,'Gemini利用条件・料金を保存')
    assert service.store.load()['card']['model']==DEFAULT_MODEL and not calls
    view(app,'企画案の生成・比較')
    assert next(b for b in app.button if b.label=='企画候補を1回生成').disabled
    next(c for c in app.checkbox if c.label=='今回1送信のモデル・最大見積・送信内容・利用条件を確認し、外部送信を承認する').check().run()
    button(app,'企画候補を1回生成');assert len(calls)==1 and not app.error
    button(app,'この企画を明示選択する');view(app,'脚本制作・自動改善')
    next(c for c in app.checkbox if c.label=='今回1送信のモデル・最大見積・送信内容・利用条件を確認し、外部送信を承認する').check().run()
    button(app,'脚本候補を1回生成')
    data=service.store.load();assert len(calls)==2 and not app.exception and not app.error
    assert data['jobs'][-1]['phase']=='evaluate' and data['jobs'][-1]['stop_reason']=='approval_required'
    candidate=data['candidates'][-1];assert len(candidate['story']['pages'])==8
    assert sum(len(p['panels']) for p in candidate['story']['pages'])==24
    assert not data['adoptions'] and service.stories.path.read_bytes()==original and SECRET not in str(app)
