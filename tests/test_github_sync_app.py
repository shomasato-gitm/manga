from pathlib import Path
from unittest.mock import patch
import pytest
from streamlit.testing.v1 import AppTest
from ui_helpers import go,element,button
from test_sync_safety import seeded

APP=Path(__file__).resolve().parents[1]/'app.py'


@pytest.fixture
def workspace(seeded,monkeypatch):
    s,r,p,*_=seeded
    monkeypatch.setenv('MANGA_DATA_DIR',str(s.directory))
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('外部通信を実行した'))
    monkeypatch.setattr('manga.github_sync_ui.service_for',lambda directory,project_id,repository:s)
    app=AppTest.from_file(str(APP),default_timeout=30).run();button(app,'選択したプロジェクトを開く')
    go(app,'完成・書き出し','GitHub作品同期')
    return app,*seeded


def view(app,value):
    element(app,'radio','同期の作業').set_value(value).run(timeout=30)
    assert not app.exception


def test_sync_screen_does_not_contact_github_on_open_or_other_screens(workspace):
    app,s,r,*_=workspace
    from manga.navigation import MENUS
    assert len(MENUS)==6
    with patch.object(r,'head',side_effect=AssertionError('画面を開いただけで通信')):
        app.run();assert not app.exception
        go(app,'企画・ストーリー','ホーム・進行状況')
    go(app,'完成・書き出し','GitHub作品同期')
    assert element(app,'radio','同期の作業').value=='GitHubから候補取得'


def test_existing_json_fetch_diff_explicit_adoption_and_export_confirmation(workspace):
    app,s,r,p,latest,art,pages,candidate,head=workspace
    story_before=pages.stories.path.read_bytes();images={path:path.read_bytes() for path in art.root.rglob('*.png')}
    element(app,'text_input','取得するブランチ').set_value('exchange').run()
    element(app,'radio','GitHubからの取得方式').set_value('既存worksファイル').run()
    button(app,'GitHubの作品ファイル一覧を取得')
    button(app,'選択したファイルを候補取得')
    assert pages.stories.path.read_bytes()==story_before
    view(app,'差分確認・明示採用')
    assert element(app,'button','選んだ内容をローカルへ明示採用').disabled
    element(app,'multiselect','正式採用する内容（初期値は未選択）').set_value(['story']).run()
    element(app,'checkbox','差分と採用範囲を確認した').check().run()
    button(app,'選んだ内容をローカルへ明示採用')
    assert len(pages.stories.load_history())==2
    assert all(path.read_bytes()==content for path,content in images.items())
    view(app,'GitHubへ保存')
    element(app,'text_input','保存先の作品同期ブランチ').set_value('exchange').run()
    button(app,'送信対象と容量を確認する計画を作成')
    assert element(app,'button','確認した作品スナップショットをGitHubへ保存').disabled
    element(app,'checkbox','この作品の表示されたファイルをGitHubへ保存する').check().run()
    button(app,'確認した作品スナップショットをGitHubへ保存')
    assert s.store.load()['exports'][-1]['status']=='保存済み'
    assert r.head('main') is None


def test_pdca_ui_stays_unconfirmed_without_real_quality_review(workspace):
    app,s,r,p,*_=workspace
    view(app,'品質・PDCA・履歴')
    element(app,'checkbox','主観的な参考評価点を記録する').check().run()
    element(app,'number_input','今回の参考評価点').set_value(100).run()
    element(app,'checkbox','現在の採用版と品質レビューを人間が最終確認した').check().run()
    button(app,'今回の改善・再評価履歴を記録')
    row=s.store.load()['iterations'][0]
    assert not row['confirmed'] and row['state']!='品質基準達成'


def test_first_remote_work_can_be_explicitly_registered_without_an_open_project(seeded,monkeypatch,tmp_path):
    from manga.github_sync import SyncService
    s,r,p,latest,art,pages,candidate,head=seeded
    fresh=SyncService(tmp_path/'empty_client',p.id,r)
    fresh.fetch_legacy('exchange',f'works/{p.id}/story.json',expected_revision=0)
    monkeypatch.setenv('MANGA_DATA_DIR',str(fresh.directory))
    monkeypatch.setattr('manga.github_sync_ui.service_for',lambda directory,project_id,repository:fresh)
    app=AppTest.from_file(str(APP),default_timeout=30).run()
    go(app,'完成・書き出し','GitHub作品同期')
    element(app,'text_input','同期する作品ID（必須）').set_value(p.id).run()
    view(app,'差分確認・明示採用')
    element(app,'multiselect','正式採用する内容（初期値は未選択）').set_value(['story']).run()
    element(app,'checkbox','差分と採用範囲を確認した').check().run()
    button(app,'選んだ内容をローカルへ明示採用')
    assert app.session_state['active_project'].id==p.id
    assert (fresh.directory/f'{p.id}.json').is_file()
    assert not app.exception


def test_existing_png_export_can_be_explicitly_retained_for_sync(workspace):
    from manga.sync_outputs import output_index,read_output
    app,s,r,p,*_=workspace
    go(app,'完成・書き出し','PNG・PDF書き出し')
    button(app,'出力ファイルを生成')
    assert not app.error,[e.value for e in app.error]
    button(app,'この完成ファイルをGitHub同期用にローカル保存')
    items=output_index(s.directory,p.id)['items']
    assert len(items)==1 and read_output(s.directory,p.id,items[0]).startswith(b'\x89PNG')
    assert s.store.load()['exports']==[]


def test_unicode_image_display_keeps_actual_git_name_and_imports(workspace):
    import unicodedata
    from review_fixtures import sample_image
    app,s,r,p,latest,art,pages,candidate,head=workspace
    actual=f'works/{p.id}/'+unicodedata.normalize('NFD','夕暮れの教室、涙色のまなざし.png')
    r.publish('exchange',head,head,{actual:sample_image()},'Unicode image fixture')
    element(app,'text_input','取得するブランチ').set_value('exchange').run()
    element(app,'radio','GitHubからの取得方式').set_value('既存worksファイル').run()
    button(app,'GitHubの作品ファイル一覧を取得')
    selector=element(app,'multiselect','取り込むカラー画像・コマ画像（任意）')
    assert selector.options==[unicodedata.normalize('NFC',actual)]
    selector.set_value([actual]).run()
    assert element(app,'multiselect','取り込むカラー画像・コマ画像（任意）').value==[actual]
    before=art.path.read_bytes()
    button(app,'選択したファイルを候補取得')
    incoming=s.store.load()['candidates'][-1]
    assert incoming['status']=='確認待ち'
    assert len([e for e in incoming['manifest']['files'] if e['path'].startswith('images/')])==1
    assert art.path.read_bytes()==before
