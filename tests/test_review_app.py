import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from manga.review_storage import ReviewStore
from manga.review_context import load_context
from manga.review_checks import make_issue
from manga.review_prompts import response_example
from manga.review_models import MANUAL_ITEMS
from review_fixtures import create_review_example
from ui_helpers import go, element, button

APP = Path(__file__).resolve().parents[1]/'app.py'


def view(app, value):
    element(app, 'radio', '品質チェックの作業').set_value(value).run(timeout=30)
    assert not app.exception


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a,**k:pytest.fail('外部通信を実行した'))
    monkeypatch.setenv('MANGA_DATA_DIR', str(tmp_path))
    project, latest, art, pages = create_review_example(tmp_path)
    app = AppTest.from_file(str(APP), default_timeout=30).run()
    button(app, '選択したプロジェクトを開く')
    go(app, '完成・書き出し', '品質チェック・修正管理')
    return app, tmp_path, project, latest, art, pages, ReviewStore(tmp_path, project.id)


def test_quality_is_inside_six_menus_and_does_not_run_hidden_checks_or_rendering(workspace):
    app, root, project, latest, art, pages, store = workspace
    from manga.navigation import MENUS
    assert len(MENUS) == 6 and '品質チェック・修正管理' in MENUS['完成・書き出し']
    assert not store.path.exists()
    with patch('manga.review_checks.scan', side_effect=AssertionError('自動検査した')), patch('manga.review_assets.render_page', side_effect=AssertionError('ページを描画した')):
        app.run(); assert not app.exception
        go(app, '企画・ストーリー', 'ホーム・進行状況')
        assert any(b.label == '品質チェック・修正管理を開く' for b in app.button)
    assert not store.path.exists()
    go(app, '完成・書き出し', '品質チェック・修正管理')
    assert any(m.label == '自動検出' and '未検査' in m.value for m in app.metric)


def test_scan_thumbnail_page_manual_preview_final_confirmation_and_reopen(workspace):
    app, root, project, latest, art, pages, store = workspace
    button(app, '自動チェックを実行')
    assert store.load()['scans'][-1]['completed']
    button(app, 'ページサムネイルを作成・更新')
    view(app, 'ページ一覧・手動レビュー')
    assert len([i for i in app.button if i.label.endswith('ページを確認')]) == 8
    button(app, '1ページを確認')
    button(app, '選択ページをプレビュー')
    button(app, 'このプレビューを目視確認した')
    button(app, '目視した7項目をすべて「問題なし」に設定')
    button(app, '手動レビューを保存')
    assert set(store.load()['manual']['1']['answers'].values()) == {'問題なし'}
    button(app, 'このページを最終確認済みにする')
    assert '1' in store.load()['finals']
    element(app, 'selectbox', '品質確認するページ').set_value(2).run()
    assert all(element(app, 'selectbox', label).value == '未確認' for label in MANUAL_ITEMS.values())
    element(app, 'selectbox', '品質確認するページ').set_value(1).run()
    assert all(element(app, 'selectbox', label).value == '問題なし' for label in MANUAL_ITEMS.values())
    go(app, 'イラスト制作', 'コマ制作ワークスペース')
    go(app, '完成・書き出し', '品質チェック・修正管理')
    assert store.load()['manual']['1']['answers']['character'] == '問題なし'
    assert not app.exception


def test_task_to_exact_image_panel_and_back_then_recheck_completion(workspace):
    app, root, project, latest, art, pages, store = workspace
    issue = make_issue(project.id, '人物の一貫性', '美咲の顔が基準画像と異なる', page=1, panel=2)
    store.create_task(issue, origin='manual', expected_revision=0, snapshot=load_context(root, project.id).snapshot)
    app.run(); view(app, '修正タスク')
    button(app, 'このタスクの対象画面へ移動')
    assert element(app, 'radio', '制作メニュー').value == 'イラスト制作'
    assert element(app, 'selectbox', '画像を確認するページ').value == 0
    assert element(app, 'selectbox', '画像を管理するコマ').value == 1
    button(app, '品質チェックへ戻る')
    assert element(app, 'radio', '品質チェックの作業').value == '修正タスク'
    element(app, 'selectbox', '修正状態').set_value('再確認待ち').run()
    element(app, 'text_area', '修正方法・対応メモ').set_value('基準画像と比較して修正した').run()
    button(app, 'タスクの状態を保存')
    element(app, 'selectbox', '修正状態').set_value('確認完了').run()
    element(app, 'checkbox', '現在の保存済み画像・設定を人間が再確認した').check().run()
    button(app, 'タスクの状態を保存')
    assert store.load()['tasks'][0]['status'] == '確認完了'


def test_bubble_issue_opens_exact_page_and_preserves_review_answers(workspace):
    app, root, project, latest, art, pages, store = workspace
    issue = make_issue(project.id, '吹き出し', '顔を隠している', page=2, panel=3, target='bubble')
    store.create_task(issue, origin='manual', expected_revision=0, snapshot=load_context(root, project.id).snapshot)
    app.run(); view(app, '修正タスク'); button(app, 'このタスクの対象画面へ移動')
    assert element(app, 'radio', '制作メニュー').value == '漫画ページ編集'
    assert element(app, 'selectbox', '合成するページ').value == 2
    button(app, '品質チェックへ戻る')
    assert not app.exception
    assert element(app, 'radio', '品質チェックの作業').value == '修正タスク'


def test_ai_file_import_default_validation_materials_and_unconfirmed_proposals(workspace, monkeypatch):
    app, root, project, latest, art, pages, store = workspace
    view(app, 'ChatGPT画像レビュー（任意）')
    assert element(app, 'radio', '画像レビューJSONの入力方法').value == 'JSONファイル'
    button(app, '画像レビューの依頼を作成')
    assert any(d.label == '依頼プロンプトをダウンロード' for d in app.get('download_button'))
    assert not any(c.label == '詳細を表示（プロンプト全文）' and c.value for c in app.checkbox)
    button(app, 'ChatGPTに渡すレビュー資料ZIPを生成')
    assert any(d.label == 'レビュー資料ZIPをダウンロード' for d in app.get('download_button'))
    request = store.load()['ai_requests'][0]
    payload = response_example(request)
    class Upload(BytesIO):
        name = 'image_review.json'
    upload = Upload(json.dumps(payload, ensure_ascii=False).encode('utf-8-sig'))
    # AppTestはfile_uploaderを操作できないため、ファイル境界だけ差し替える。
    monkeypatch.setattr('streamlit.file_uploader', lambda *a,**k:upload)
    app.run(); button(app, '画像レビューJSONを検証・保存')
    assert store.load()['ai_reviews'][0]['decisions'][store.load()['ai_reviews'][0]['issues'][0]['id']] == '未確認'
    assert store.load()['tasks'] == []
    button(app, 'このAI指摘を確認して修正タスクにする')
    assert len(store.load()['tasks']) == 1
    button(app, 'このAIレビュー結果を読んで確認した')
    assert store.load()['ai_reviews'][0]['reviewed']


def test_incomplete_subjective_review_warns_but_allows_explicit_export(workspace):
    app, root, project, latest, art, pages, store = workspace
    assert element(app, 'button', 'PNG・PDF・縦読み書き出しへ').disabled
    element(app, 'checkbox', '未確認項目の警告を確認して書き出しへ進む').check().run()
    button(app, 'PNG・PDF・縦読み書き出しへ')
    assert any('未確認' in w.value for w in app.warning)
    element(app, 'selectbox', '出力形式').set_value('page').run()
    button(app, '出力ファイルを生成')
    assert not app.error, [e.value for e in app.error]
    assert any(d.label == '完成ファイルをダウンロード' for d in app.get('download_button'))
    assert not store.load()['finals']
