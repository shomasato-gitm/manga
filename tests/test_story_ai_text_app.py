import json
from streamlit.testing.v1 import AppTest
from manga.story_ai_service import StoryAIService
from manga.story_ai_mock import MockStoryAI
from story_ai_fixtures import ai_workspace
from test_story_ai_gemini import card,MODEL
from test_story_ai_app import open_studio,view
from ui_helpers import button


def test_default_manual_and_missing_free_terms_do_not_send(ai_workspace,monkeypatch):
    service=ai_workspace
    monkeypatch.setattr(StoryAIService,'create_job',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('implicit API')))
    app=open_studio()
    assert next(s for s in app.selectbox if s.label=='企画・脚本の生成方法').value=='manual'
    assert any('STEP 1' in c.value and '7' in c.value for c in app.caption)
    view(app,'企画案の生成・比較');assert not any(b.label=='企画案を自動生成' for b in app.button)
    next(s for s in app.selectbox if s.label=='企画・脚本の生成方法').set_value('verified-free').run()
    assert any('料金設定' in i.value for i in app.info) and not service.store.load()['jobs']


def test_gemini_single_send_preview_approval_comparison(ai_workspace,monkeypatch):
    import manga.story_ai_gemini as module
    service=ai_workspace;service.configure(service.store.load()['settings'],card(),expected_revision=service.store.load()['revision'])
    monkeypatch.setenv('MANGA_ENABLE_PAID_STORY','1');monkeypatch.setenv('GEMINI_API_KEY','TEST-KEY-MUST-NOT-APPEAR')
    monkeypatch.setattr(StoryAIService,'start',lambda self,i:self.run_job(i));calls=[]
    def send(body,key):
        calls.append(body);request=service.store.load()['requests'][-1];text=MockStoryAI().generate(request)['text']
        return dict(modelVersion=MODEL,usageMetadata=dict(promptTokenCount=1,candidatesTokenCount=1,totalTokenCount=2),candidates=[dict(finishReason='STOP',content=dict(parts=[dict(text=text)]))])
    monkeypatch.setattr(module,'transport',send)
    app=open_studio();next(s for s in app.selectbox if s.label=='企画・脚本の生成方法').set_value('paid').run()
    view(app,'企画案の生成・比較');assert not calls
    assert next(b for b in app.button if b.label=='企画候補を1回生成').disabled
    next(c for c in app.checkbox if c.label=='今回1送信のモデル・最大見積・送信内容・利用条件を確認し、外部送信を承認する').check().run()
    button(app,'企画候補を1回生成');button(app,'この企画を明示選択する')
    view(app,'脚本制作・自動改善')
    next(c for c in app.checkbox if c.label=='今回1送信のモデル・最大見積・送信内容・利用条件を確認し、外部送信を承認する').check().run()
    button(app,'脚本候補を1回生成');assert len(calls)==2
    assert service.store.load()['jobs'][-1]['stop_reason']=='approval_required'
    next(c for c in app.checkbox if c.label=='この1送信だけの費用・送信内容を承認する').check().run()
    button(app,'承認した次の1送信だけを実行');assert len(calls)==3
    view(app,'比較・採用・履歴');assert not app.exception and not app.error
    assert not service.store.load()['adoptions']
    assert 'TEST-KEY-MUST-NOT-APPEAR' not in str(app) and 'TEST-KEY-MUST-NOT-APPEAR' not in service.store.path.read_text()
