import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from manga.storage import ProjectStore
from manga.story_studio import StudioService
from manga.story_studio_samples import sample_studio_response
from manga.story_studio_ui_common import NAMES
from ui_helpers import go, element, button

APP = Path(__file__).resolve().parents[1]/'app.py'


@pytest.fixture
def studio_app(tmp_path, manga_project, monkeypatch):
    ProjectStore(tmp_path).save(manga_project)
    monkeypatch.setenv('MANGA_DATA_DIR', str(tmp_path))
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('外部へ自動送信した'))
    app = AppTest.from_file(str(APP), default_timeout=40).run()
    button(app, '選択したプロジェクトを開く')
    go(app, '企画・ストーリー', 'ストーリー制作スタジオ')
    return app, StudioService(tmp_path, manga_project.id)


def reply(app, service, kind, score=7):
    req = next(r for r in reversed(service.concepts.load()['requests']) if r['kind'] == kind)
    label = NAMES[kind]
    method = element(app, 'radio', label+'の入力方法')
    assert method.value == 'JSONファイル'
    method.set_value('貼り付け').run()
    element(app, 'text_area', label+'を貼り付け').set_value(json.dumps(sample_studio_response(req, score), ensure_ascii=False)).run()
    button(app, '回答JSONを検証して保存')
    assert not app.error, [error.value for error in app.error]


def test_legacy_evaluation_view_cannot_improve_from_old_evaluation(studio_app, story):
    from manga.editorial_prompts import evaluation_template
    app, service = studio_app
    version = service.stories.save(story, expected_version_id=None)
    payload = evaluation_template(version['id'])
    first = service.editorial.save_evaluation(version['id'], json.dumps(payload), expected_revision=0)
    second = service.editorial.save_evaluation(version['id'], json.dumps(payload), expected_revision=1)
    go(app, '企画・ストーリー', 'ストーリー評価・改善')
    chooser = element(app, 'selectbox', '使用する評価履歴')
    assert chooser.value == second['id']
    chooser.set_value(first['id']).run()
    assert any('過去の評価は閲覧用' in item.value for item in app.warning)
    assert not any(b.label == '改善版を検証して新しい版に保存' for b in app.button)
    third = service.editorial.save_evaluation(version['id'], json.dumps(payload), expected_revision=2)
    app.run()
    assert element(app, 'selectbox', '使用する評価履歴').value == third['id']
    assert not app.exception


def test_three_concepts_to_three_pdca_cycles_and_finalization_in_one_workspace(studio_app):
    app, service = studio_app
    button(app, '概要3案の依頼を作成')
    reply(app, service, 'concepts')
    choices = [b for b in app.button if b.label == 'この概要案を採用']
    assert len(choices) == 3
    choices[1].click().run()
    assert not app.exception
    selected = service.concepts.load()['selections'][-1]
    assert selected['concept_id'] == service.concepts.load()['batches'][0]['payload']['concepts'][1]['id']
    assert element(app, 'radio', '制作ステップ').value == '詳細脚本'
    button(app, '採用概要から詳細脚本の依頼を作成')
    reply(app, service, 'detail')
    assert element(app, 'radio', '制作ステップ').value == '評価・改善'
    for points in (6, 7, 8):
        button(app, '現在版の評価依頼を作成')
        reply(app, service, 'evaluation', points)
        assert any(b.label == '最新評価から改善依頼を作成' for b in app.button)
        button(app, '最新評価から改善依頼を作成')
        reply(app, service, 'improvement')
    button(app, '現在版の評価依頼を作成')
    reply(app, service, 'evaluation', 9)
    button(app, '次の工程を開く')
    assert element(app, 'radio', '制作ステップ').value == '比較・確定'
    assert element(app, 'button', '現在のストーリーを最終確定').disabled
    element(app, 'checkbox', 'テーマ・世界観・人物設定・独自性・変更箇所を人間が最終確認した').check().run()
    button(app, '現在のストーリーを最終確定')
    assert len(service.stories.load_history()) == 4
    assert len(service.concepts.load()['improvements']) == 3
    assert service.editorial.load()['finalizations'][-1]['story_version_id'] == service.stories.load_history()[-1]['id']
    assert any(b.label == 'キャラクター・作画工程へ進む' for b in app.button)
    fresh=AppTest.from_file(str(APP),default_timeout=40).run()
    button(fresh,'選択したプロジェクトを開く')
    go(fresh,'企画・ストーリー','ストーリー制作スタジオ')
    assert element(fresh,'radio','制作ステップ').value=='比較・確定'
    assert any(b.label=='キャラクター・作画工程へ進む' for b in fresh.button)


def test_view_switch_preserves_draft_and_does_not_generate_requests(studio_app):
    app, service = studio_app
    button(app, '概要3案の依頼を作成')
    element(app, 'radio', '概要3案JSONの入力方法').set_value('貼り付け').run()
    element(app, 'text_area', '概要3案JSONを貼り付け').set_value('入力途中のJSON').run()
    element(app, 'radio', '制作ステップ').set_value('参考作品・技法').run()
    element(app, 'radio', '制作ステップ').set_value('概要3案・選択').run()
    assert element(app, 'text_area', '概要3案JSONを貼り付け').value == '入力途中のJSON'
    assert len(service.concepts.load()['requests']) == 1
    app.run()
    assert not app.exception


def test_bad_json_shows_error_without_creating_batch(studio_app):
    app, service = studio_app
    button(app, '概要3案の依頼を作成')
    element(app, 'radio', '概要3案JSONの入力方法').set_value('貼り付け').run()
    element(app, 'text_area', '概要3案JSONを貼り付け').set_value('{broken').run()
    button(app, '回答JSONを検証して保存')
    assert app.error and service.concepts.load()['batches'] == []


def test_current_analysis_techniques_can_be_selected_and_reused_from_studio(studio_app):
    from test_story_studio import add_reference
    app,service=studio_app
    ref=add_reference(service)
    element(app,'radio','制作ステップ').set_value('参考作品・技法').run()
    element(app,'radio','参考作品の作業').set_value('使用技法の選択').run()
    element(app,'multiselect','概要・詳細・評価・改善で使う参考作品（最大3作品・任意）').set_value([ref['id']]).run()
    element(app,'multiselect',ref['title']+'：使用する抽象技法').set_value(['opening']).run()
    button(app,'使用する参考技法を保存')
    button(app,'概要3案の依頼を作成')
    req=service.concepts.load()['requests'][-1]
    assert req['context']['references'][0]['analysis_id']
    assert [t['id'] for t in req['context']['references'][0]['techniques']]==['opening']


def test_additional_improvement_needs_explicit_ui_approval(studio_app):
    from test_story_studio import detail,request,answer
    app,service=studio_app
    detail(service)
    service.set_policy(85,1,expected_revision=service.concepts.load()['revision'])
    answer(service,request(service,'evaluation'))
    answer(service,request(service,'improvement'))
    answer(service,request(service,'evaluation'))
    element(app,'radio','制作ステップ').set_value('評価・改善').run()
    assert not any(b.label=='最新評価から改善依頼を作成' for b in app.button)
    element(app,'checkbox','追加改善を行う').check().run()
    assert service.concepts.load()['extensions']==[]
    element(app,'text_input','追加改善の理由（必須）').set_value('導入の演出を再検討する').run()
    button(app,'追加改善を承認して保存')
    assert service.concepts.load()['extensions'][-1]['limit']==2
    assert any(b.label=='最新評価から改善依頼を作成' for b in app.button)
