import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest
from manga.page_layout import default_bubble
from manga.page_storage import PageStore
from manga.storage import StorageError
from page_editor_fixtures import create_example
from ui_helpers import go,element,button

APP=Path(__file__).resolve().parents[1]/'app.py'


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    import socket
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('外部通信を実行した'))
    monkeypatch.setenv('MANGA_DATA_DIR',str(tmp_path))
    project,latest,images,store=create_example(tmp_path)
    app=AppTest.from_file(str(APP),default_timeout=20).run()
    button(app,'選択したプロジェクトを開く')
    go(app,'漫画ページ編集')
    assert not app.exception
    return app,project,latest,images,store


def draft(app,project,latest):
    return app.session_state['_page_editor_drafts'][(project.id,1,latest['id'])]


def target(app,value):
    element(app,'selectbox','編集する対象').set_value(value).run(timeout=20)
    assert not app.exception


def test_visual_crop_bubble_narration_save_export_and_reload(workspace):
    app,project,latest,images,store=workspace
    assert element(app,'radio','編集方法').value=='ビジュアル編集'
    original_story=store.stories.path.read_bytes();art_before=images.path.read_bytes()
    original_files={p:p.read_bytes() for p in images.root.rglob('*.png')}
    target(app,'image:1')
    assert element(app,'selectbox','画像の表示方式').value=='cover'
    element(app,'slider','画像の縦位置（上端 → 下端）').set_value(30.0).run()
    element(app,'slider','画像のズーム倍率').set_value(1.25).run()
    assert '1' not in store.load()['pages']
    config=draft(app,project,latest)['config']
    assert config['image_placements']['1']['zoom']==1.25
    assert len(config['bubbles'])==4
    speaker=next(b for b in config['bubbles'] if b['speaker']=='悠真')
    target(app,speaker['id'])
    element(app,'text_area','本文（ページ専用）').set_value('あのさ、美咲。話があるんだ。').run()
    element(app,'slider','横位置（%）').set_value(50.0).run()
    element(app,'slider','横幅（%）').set_value(45.0).run()
    narration=next(b for b in draft(app,project,latest)['config']['bubbles'] if b['kind']=='narration')
    target(app,narration['id'])
    element(app,'slider','背景の不透明度（%）').set_value(65).run()
    element(app,'color_picker','枠の背景色').set_value('#f0ebdf').run()
    button(app,'ページの編集を保存')
    assert not app.exception
    saved=store.load()['pages']['1']
    assert saved['image_placements']['1']['zoom']==1.25
    assert next(b for b in saved['bubbles'] if b['kind']=='narration')['opacity']==.65
    assert next(b for b in saved['bubbles'] if b['speaker']=='悠真')['text']=='あのさ、美咲。話があるんだ。'
    button(app,'ページをプレビュー・再描画')
    assert not app.error,[e.value for e in app.error]
    go(app,'完成・書き出し','PNG・PDF書き出し')
    for kind in ('page','pdf','sns'):
        element(app,'selectbox','出力形式').set_value(kind).run()
        button(app,'出力ファイルを生成')
        assert not app.error,[e.value for e in app.error]
        assert any(d.label=='完成ファイルをダウンロード' for d in app.get('download_button'))
    assert store.stories.path.read_bytes()==original_story and images.path.read_bytes()==art_before
    assert all(p.read_bytes()==content for p,content in original_files.items())
    fresh=AppTest.from_file(str(APP)).run();button(fresh,'選択したプロジェクトを開く');go(fresh,'漫画ページ編集')
    assert element(fresh,'slider','画像のズーム倍率').value==1.25


def test_drag_event_is_draft_only_and_cancel_undo_work(workspace,monkeypatch):
    app,project,latest,_,store=workspace
    b=draft(app,project,latest)['config']['bubbles'][0]
    box=dict(x=.2,y=.04,width=.55,height=.18)
    events=[dict(type='move',id=b['id'],box=box,event_id='one')]
    def canvas(*args,**kwargs):
        return dict(events.pop(0),token=args[4]) if events else None
    monkeypatch.setattr('manga.page_editor_ui.page_canvas',canvas)
    app.run();assert not app.exception
    assert draft(app,project,latest)['config']['bubbles'][0]['box']==box
    assert store.load()['pages']=={}
    assert element(app,'selectbox','編集する対象').value==b['id']
    button(app,'下書きを1つ戻す')
    assert draft(app,project,latest)['config']['bubbles'][0]['box']==b['box']
    target(app,'image:2');element(app,'slider','画像のズーム倍率').set_value(2.0).run()
    go(app,'企画・ストーリー','ホーム・進行状況');go(app,'漫画ページ編集')
    target(app,'image:2');assert element(app,'slider','画像のズーム倍率').value==2.0
    button(app,'キャンセル（保存済みに戻す）')
    target(app,'image:2');assert element(app,'slider','画像のズーム倍率').value==1.0
    assert store.load()['pages']=={}


def test_old_bubbles_remain_until_explicit_proposal_application_and_save(workspace):
    app,project,latest,_,store=workspace
    page=latest['story']['pages'][0]
    old=dict(template='hero_top',frames=None,source_page=page,source_version_id=latest['id'],bubbles=[default_bubble(project.id,1,p['panel'],source) for p in page['panels'] for source in ('dialogue','narration')])
    store.save_page(1,old,story_version_id=latest['id'],expected_revision=1)
    app.run();before=store.path.read_bytes()
    assert element(app,'selectbox','画像の表示方式').value=='contain'
    button(app,'脚本から吹き出しを提案')
    assert len(draft(app,project,latest)['config']['bubbles'])==6
    assert store.path.read_bytes()==before
    assert element(app,'button','分割・サイズ調整案を下書きへ適用').disabled
    element(app,'checkbox','既存吹き出しを置き換える内容を確認しました').check().run()
    button(app,'分割・サイズ調整案を下書きへ適用')
    assert len(draft(app,project,latest)['config']['bubbles'])==4
    assert store.path.read_bytes()==before
    button(app,'ページの編集を保存')
    assert len(store.load()['pages']['1']['bubbles'])==4
    assert store.load()['history'][-1]['before']==old
    element(app,'checkbox','履歴を現在のページへ復元する').check().run()
    button(app,'この編集履歴を復元')
    assert store.load()['pages']['1']==old


def test_failed_save_and_conflict_keep_draft_and_saved_bytes(workspace):
    app,project,latest,_,store=workspace
    element(app,'slider','画像のズーム倍率').set_value(1.5).run()
    before=store.path.read_bytes()
    with patch('manga.page_storage.write_json_atomic',side_effect=StorageError('容量不足')):
        button(app,'ページの編集を保存')
    assert any('容量不足' in e.value for e in app.error)
    assert draft(app,project,latest)['config']['image_placements']['1']['zoom']==1.5
    assert store.path.read_bytes()==before
    store.save_settings(dict(store.load()['settings'],dpi=72),expected_revision=1)
    app.run();before=store.path.read_bytes()
    button(app,'ページの編集を保存')
    assert any('更新' in e.value for e in app.error)
    assert store.path.read_bytes()==before
    assert element(app,'slider','画像のズーム倍率').value==1.5


def test_preview_uses_cache_and_invalid_box_is_warning_not_saved(workspace):
    app,project,latest,_,store=workspace
    from manga.page_editor_preview import render_page
    with patch('manga.page_editor_preview.render_page',wraps=render_page) as render:
        app.run();assert render.call_count==0
        element(app,'slider','画像のズーム倍率').set_value(1.2).run();assert render.call_count==1
        app.run();assert render.call_count==1
    b=draft(app,project,latest)['config']['bubbles'][0];target(app,b['id'])
    element(app,'slider','横位置（%）').set_value(95.0).run()
    assert any('枠の外' in e.value for e in app.error)
    assert element(app,'button','ページの編集を保存').disabled
    assert store.load()['pages']=={}
    button(app,'下書きを1つ戻す')
    assert not app.error,[e.value for e in app.error]


def test_story_version_change_does_not_apply_old_draft(workspace):
    app,project,latest,_,store=workspace
    element(app,'slider','画像のズーム倍率').set_value(1.8).run()
    changed=copy.deepcopy(latest['story']);changed['pages'][0]['panels'][0]['description']='夕暮れから夜の教室へ。'
    store.stories.save(changed,expected_version_id=latest['id'])
    app.run()
    assert any('古いストーリー版' in w.value for w in app.warning)
    assert element(app,'slider','画像のズーム倍率').value==1.0
    assert any(d.label=='古い版のページ下書きを取得' for d in app.get('download_button'))
    assert store.load()['pages']=={}


def test_browsing_legacy_bubbles_does_not_change_their_appearance(workspace):
    app,project,latest,_,store=workspace
    page=latest['story']['pages'][0]
    old=dict(template='hero_top',frames=None,source_page=page,source_version_id=latest['id'],bubbles=[default_bubble(project.id,1,p['panel'],source) for p in page['panels'] for source in ('dialogue','narration')])
    old['bubbles'][1].update(shape='ellipse',tail=True)
    store.save_page(1,old,story_version_id=latest['id'],expected_revision=1)
    app.run();before=store.path.read_bytes()
    for b in old['bubbles']:
        target(app,b['id'])
        assert draft(app,project,latest)['config']==old
    assert store.path.read_bytes()==before
