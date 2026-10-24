from pathlib import Path

from streamlit.testing.v1 import AppTest

from manga.character_ai_proposals import CharacterProposals
from manga.character_ai_service import CharacterAIService
from character_ai_fixtures import character_workspace, bible, hashes
from ui_helpers import go, button

APP = Path(__file__).resolve().parents[1]/'app.py'


def open_studio():
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    button(app, '選択したプロジェクトを開く')
    go(app, 'キャラクター・作風', 'AIキャラクター制作・一貫性管理')
    assert not app.exception
    return app


def element(app, kind, label):
    return next(e for e in getattr(app, kind) if e.label == label)


def view(app, value):
    element(app, 'radio', 'キャラクター制作の段階').set_value(value).run(timeout=60)
    assert not app.exception


def test_extract_identify_adopt_bible_check_and_unified_prompt_in_ui(character_workspace):
    _, service = character_workspace
    person = service.artwork.load()['characters'][0]
    original = service.artwork.path.read_bytes()
    app = open_studio()
    assert len(element(app, 'radio', '制作メニュー').options) == 6
    button(app, '無料モックで人物候補・設定案を生成')
    assert not app.error and service.artwork.path.read_bytes() == original
    view(app, '候補確認・採用')
    element(app, 'selectbox', '同定した反映先（自動選択しません）').select(person['id']).run(timeout=60)
    element(app, 'checkbox', '人物の同定・原文・提案と事実の区別・選択項目を確認した').check().run(timeout=60)
    button(app, 'この人物の選択項目だけを正式採用')
    assert not app.error and len(service.artwork.load()['characters']) == 1
    assert service.artwork.load()['characters'][0]['primary_reference_id'] == person['primary_reference_id']
    view(app, 'キャラクターバイブル')
    element(app, 'text_area', '今回の確認・採用根拠（必須）').set_value('人物の固定特徴・正面基準画像を確認').run(timeout=60)
    element(app, 'checkbox', '固定設定・別名・参照状態・場面例外を確認した').check().run(timeout=60)
    button(app, '確認済みバイブルを新しい版として保存')
    assert not app.error and len(service.store.load()['bibles']) == 1
    view(app, '矛盾チェック'); button(app, '静的一貫性チェックを実行')
    assert not app.error and service.store.load()['checks'][-1]['panels'] == 24
    view(app, '画像プロンプト'); button(app, '確認済み設定で画像プロンプトを作成')
    assert not app.error and any(d.label == '依頼プロンプトをダウンロード' for d in app.get('download_button'))
    button(app, 'このプロンプトを制作履歴へ保存')
    assert len(service.store.load()['prompts']) == 1
    assert service.artwork.load()['characters'][0]['id'] == person['id']


def test_conflict_issue_can_open_exact_panel_workspace(character_workspace):
    project, service = character_workspace
    bible(character_workspace)
    person = service.artwork.load()['characters'][0]
    service.artwork.save_direction(1, 1, dict(character_ids=[person['id']], composition='悠真：髪色=金'), story_version_id=service.proposals.latest()['id'], expected_revision=service.artwork.load()['revision'])
    service.run_checks(expected_revision=service.store.load()['revision'])
    app = open_studio(); view(app, '矛盾チェック')
    selection = element(app, 'selectbox', '詳しく確認する指摘')
    index = next(i for i, issue in enumerate(service.store.load()['checks'][-1]['issues']) if issue['kind'] == 'fixed_conflict')
    selection.select(index).run(timeout=60)
    button(app, 'このコマの作画指示・画像を確認する')
    assert not app.exception and element(app, 'radio', '制作メニュー').value == 'イラスト制作'
    assert any('1ページ' in h.value and '1コマ' in h.value for h in app.subheader)
    button(app, 'このコマのバイブル付き統一プロンプトへ')
    assert element(app, 'radio', 'キャラクター制作の段階').value == '画像プロンプト'
    assert element(app, 'radio', '画像プロンプトの対象').value == 'panel'
    assert element(app, 'selectbox', 'プロンプトのページ').value == 1
    assert element(app, 'selectbox', 'プロンプトのコマ').value == 1


def test_drafts_preserved_and_hidden_screen_never_extracts_or_hashes(character_workspace, monkeypatch):
    app = open_studio()
    element(app, 'text_area', '追加して確認する人物名（任意・1行1名、脚本に記述が必要）').set_value('美咲').run()
    def forbidden(*args, **kwargs): raise AssertionError('Hidden screen must not perform work')
    monkeypatch.setattr(CharacterProposals, 'create_mock', forbidden)
    monkeypatch.setattr(CharacterAIService, 'snapshot', forbidden)
    go(app, '企画・ストーリー', 'ホーム・進行状況')
    go(app, 'キャラクター・作風', 'AIキャラクター制作・一貫性管理')
    assert element(app, 'text_area', '追加して確認する人物名（任意・1行1名、脚本に記述が必要）').value == '美咲'
    assert not app.exception


def test_unfinalized_story_and_invalid_metadata_are_protected_in_ui(character_workspace):
    _, service = character_workspace
    latest = service.proposals.latest(); service.stories.save(latest['story'], expected_version_id=latest['id'])
    app = open_studio()
    assert element(app, 'button', '無料モックで人物候補・設定案を生成').disabled
    assert any('未確定' in w.value for w in app.warning)
    service.store.path.write_text('{broken', encoding='utf-8')
    before = hashes(service.store.root)
    app.run(timeout=60)
    assert app.error and not app.exception and hashes(service.store.root) == before


def test_reference_image_is_not_read_until_requested(character_workspace, monkeypatch):
    _, service = character_workspace
    bible(character_workspace)
    app = open_studio()
    from manga.artwork_storage import ArtworkStore
    def forbidden(*args, **kwargs): raise AssertionError('Do not read original images without a click')
    monkeypatch.setattr(ArtworkStore, 'image_bytes', forbidden)
    view(app, 'キャラクターバイブル')
    assert not app.exception and not app.error and not app.get('imgs')


def test_invalid_reference_preview_reports_error_without_changing_data(character_workspace):
    _, service = character_workspace
    bible(character_workspace)
    person = service.artwork.load()['characters'][0]
    path = service.artwork.image_path(person['references'][0])
    path.write_bytes(b'corrupt image fixture')
    before = hashes(service.store.root)
    app = open_studio(); view(app, 'キャラクターバイブル')
    element(app, 'checkbox', '参照画像を1枚だけ表示・取得する').check().run(timeout=60)
    assert not app.exception and any('正常に表示できません' in e.value for e in app.error)
    assert hashes(service.store.root) == before
