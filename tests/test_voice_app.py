import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from manga.voice_models import EMOTIONS
from ui_helpers import go,element,button
from voice_fixtures import voice_workspace,all_ids

APP=Path(__file__).resolve().parents[1]/'app.py'


def open_voice(service):
    app=AppTest.from_file(str(APP),default_timeout=30).run()
    button(app,'選択したプロジェクトを開く')
    go(app,'完成・書き出し','音声制作・字幕')
    return app


def view(app,name):
    next(r for r in app.radio if r.label=='音声制作の作業').set_value(name).run()
    assert not app.exception


def test_mock_generation_preview_and_subtitle_export_via_ui(voice_workspace):
    service,*_=voice_workspace
    app=open_voice(service)
    assert any('有料TTSは無効' in i.value for i in app.info)
    view(app,'音声生成・試聴')
    button(app,'選択した音声を生成・保存／キャッシュ再利用')
    assert not app.exception and not app.error
    assert len(service.store.load()['assets'])==5
    next(c for c in app.checkbox if c.label=='選択した音声を試聴・WAV取得').check().run()
    assert not app.exception
    next(c for c in app.checkbox if c.label=='試聴して人物・読み・感情を確認した').check().run()
    button(app,'この音声を確認済みにする')
    assert service.store.load()['audio_reviews']
    view(app,'字幕・タイムライン')
    button(app,'字幕・タイムラインを新しい版で保存')
    assert not app.exception and not app.error
    assert len(service.store.load()['timelines'])==1
    assert any(d.label=='字幕SRTをダウンロード' for d in app.get('download_button'))


def test_invalid_pricing_uses_file_default_and_does_not_overwrite(voice_workspace):
    service,*_=voice_workspace
    app=open_voice(service)
    method=next(r for r in app.radio if r.label=='TTS料金設定JSONの入力方法')
    assert method.value=='JSONファイル'
    method.set_value('貼り付け').run()
    next(c for c in app.checkbox if c.label=='公式の最新料金・商用利用条件・日本語対応・最大請求量を確認した').check()
    next(t for t in app.text_area if t.label=='TTS料金設定JSONを貼り付け').set_value('{"bad": true}')
    before=service.store.path.read_bytes()
    button(app,'確認済み料金設定を保存')
    assert app.error and not app.exception
    assert service.store.path.read_bytes()==before


def test_per_line_emotion_and_reading_form_preserves_source(voice_workspace):
    service,*_=voice_workspace
    before=service.stories.path.read_bytes()
    app=open_voice(service);view(app,'音声生成・試聴')
    next(t for t in app.text_area if t.label=='読み上げ文・読み（字幕の原文は保持）').set_value('そつぎょうまで、あと、なのか。')
    next(s for s in app.selectbox if s.label=='この発話の感情').set_value('sad')
    next(c for c in app.checkbox if c.label=='話者と読み上げ文を確認した').check()
    button(app,'発話設定を保存')
    assert not app.exception and not app.error
    row=service.plan()['rows'][0]
    assert row['spoken_text']=='そつぎょうまで、あと、なのか。' and row['settings']['emotion']=='sad'
    assert service.stories.path.read_bytes()==before


def test_history_restores_existing_candidate_without_new_job(voice_workspace):
    service,*_=voice_workspace
    service.generate(all_ids(service)[:1])
    service.generate(all_ids(service)[:1],regenerate=True)
    app=open_voice(service);view(app,'履歴・復旧')
    old=service.store.load()['jobs'][0]
    next(s for s in app.selectbox if s.label=='確認する音声ジョブ').set_value(old['id']).run()
    button(app,'現在の発話へこの保存候補を採用')
    assert not app.exception and not app.error
    assert service.plan()['rows'][0]['asset']['job_id']==old['id']
    assert len(service.store.load()['jobs'])==2
