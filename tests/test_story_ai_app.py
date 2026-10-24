from pathlib import Path

from streamlit.testing.v1 import AppTest

from manga.story_ai_service import StoryAIService
from story_ai_fixtures import ai_workspace
from ui_helpers import go,button

APP=Path(__file__).resolve().parents[1]/'app.py'


def open_studio():
    app=AppTest.from_file(str(APP),default_timeout=60).run();button(app,'選択したプロジェクトを開く')
    go(app,'企画・ストーリー','AI企画・ストーリー');return app


def view(app,name):
    next(r for r in app.radio if r.label=='AIストーリーの制作段階').set_value(name).run(timeout=60)
    assert not app.exception


def test_mock_concepts_five_cycles_comparison_and_adoption_via_ui(ai_workspace,monkeypatch):
    service=ai_workspace;history=service.stories.load_history()
    monkeypatch.setattr(StoryAIService,'start',lambda self,identifier:self.run_job(identifier))
    app=open_studio();assert not app.exception and len(next(r for r in app.radio if r.label=='制作メニュー').options)==6
    assert next(s for s in app.selectbox if s.label=='企画・脚本の生成方法').value=='mock'
    view(app,'企画案の生成・比較');button(app,'企画案を自動生成');button(app,'この企画を明示選択する')
    assert not app.exception and not app.error
    view(app,'脚本制作・自動改善');button(app,'脚本生成・評価・自動改善を開始')
    assert not app.exception and not app.error and len(service.store.load()['candidates'])==6
    view(app,'比較・採用・履歴')
    assert not app.exception and any(d.label=='候補脚本JSONを取得（既存schema_version:1）' for d in app.get('download_button'))
    next(c for c in app.checkbox if c.label=='この候補の脚本・評価・差分と、後工程への影響を確認した').check().run()
    button(app,'この候補を正式脚本の新しい版として採用')
    assert not app.exception and not app.error and len(service.stories.load_history())==len(history)+1
    assert any('正式採用済み' in str(table.value) for table in app.dataframe)
    assert service.editorial.load()['evaluations'][-1]['payload']['story_version_id']==service.stories.load_history()[-1]['id']


def test_invalid_pricing_file_default_preserves_data(ai_workspace):
    service=ai_workspace;app=open_studio()
    method=next(r for r in app.radio if r.label=='テキストAPI料金JSONの入力方法');assert method.value=='JSONファイル'
    method.set_value('貼り付け').run()
    next(t for t in app.text_area if t.label=='テキストAPI料金JSONを貼り付け').set_value('{"api_key":"do-not-save"}')
    next(c for c in app.checkbox if c.label=='モデルの提供・構造化出力・商用条件・モデル最大入力幅と全加算料金を公式確認した').check()
    before=service.store.path.read_bytes();button(app,'モデル・料金設定を保存')
    assert app.error and not app.exception and service.store.path.read_bytes()==before


def test_inputs_survive_menu_switch_and_paid_remains_disabled(ai_workspace):
    service=ai_workspace;app=open_studio()
    next(t for t in app.text_input if t.label=='希望する雰囲気').set_value('静かな余韻').run()
    go(app,'完成・書き出し','API予算管理');go(app,'企画・ストーリー','AI企画・ストーリー')
    assert next(t for t in app.text_input if t.label=='希望する雰囲気').value=='静かな余韻'
    assert any('有料APIは無効' in i.value for i in app.info)


def test_non_visible_studio_never_starts_api(ai_workspace,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Generation is explicit, never a screen side effect')
    monkeypatch.setattr(StoryAIService,'create_job',forbidden)
    app=open_studio();go(app,'企画・ストーリー','ホーム・進行状況');assert not app.exception
