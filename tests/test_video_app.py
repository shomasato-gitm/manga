from pathlib import Path

from streamlit.testing.v1 import AppTest

from manga.video_service import VideoService
from video_fixtures import video_workspace,protected
from ui_helpers import go,button

APP=Path(__file__).resolve().parents[1]/'app.py'


def open_video():
    app=AppTest.from_file(str(APP),default_timeout=60).run()
    button(app,'選択したプロジェクトを開く');go(app,'完成・書き出し','動画制作・MP4出力')
    assert not app.exception
    return app


def view(app,name):
    next(r for r in app.radio if r.label=='動画制作の作業').set_value(name).run(timeout=60)
    assert not app.exception


def test_editor_preview_save_cancel_and_history_restore(video_workspace):
    service,timeline=video_workspace;before=protected(service);app=open_video()
    assert len(next(r for r in app.radio if r.label=='制作メニュー').options)==6
    next(s for s in app.selectbox if s.label=='編集する動画形式').set_value('youtube_shorts').run()
    next(t for t in app.text_input if t.label=='冒頭のフック文（任意・字幕とは別）').set_value('新しい冒頭').run()
    button(app,'このシーンの軽量プレビュー');assert not app.exception and not app.error
    button(app,'3形式の編集計画を保存');assert service.plans(timeline)['youtube_shorts']['hook']=='新しい冒頭'
    next(t for t in app.text_input if t.label=='冒頭のフック文（任意・字幕とは別）').set_value('保存しない').run()
    button(app,'未保存の編集をキャンセル')
    assert next(t for t in app.text_input if t.label=='冒頭のフック文（任意・字幕とは別）').value=='新しい冒頭'
    view(app,'出力履歴');button(app,'過去の編集計画を新しい履歴として復元')
    assert not app.exception and protected(service)==before


def test_real_generation_preview_download_and_free_repeat_via_ui(video_workspace,monkeypatch):
    import manga.video_renderer as renderer
    service,timeline=video_workspace;before=protected(service)
    # Synchronous driver for deterministic AppTest; production uses a worker thread.
    monkeypatch.setattr(VideoService,'start',lambda self,job_id:self.run_job(job_id))
    app=open_video();view(app,'生成・進行状況')
    next(m for m in app.multiselect if m.label=='出力する動画').set_value(['tiktok_vertical']).run()
    button(app,'素材・版・字幕を検査')
    next(c for c in app.checkbox if c.label=='実尺・素材と未確認事項を確認した。生成後も公開前の目視・試聴が必要').check().run()
    button(app,'選択形式の動画生成を開始')
    assert not app.exception and not app.error and len(service.store.load()['exports'])==1
    view(app,'出力履歴')
    next(c for c in app.checkbox if c.label=='選択したMP4を読み込む（1ファイルのみ）').check().run()
    assert app.get('video') and any(d.label=='MP4をダウンロード' for d in app.get('download_button'))
    monkeypatch.setattr(renderer,'run',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Cached export must not rerender')))
    view(app,'生成・進行状況');button(app,'選択形式の動画生成を開始')
    assert not app.exception and service.store.load()['jobs'][-1]['cache_reused']
    assert protected(service)==before


def test_missing_tools_does_not_break_manga_or_voice_screen(video_workspace,monkeypatch):
    import manga.video_ui as ui
    monkeypatch.setattr(ui,'video_ready',lambda:False)
    app=open_video();assert any('ffmpegとffprobe' in w.value for w in app.warning)
    go(app,'完成・書き出し','音声制作・字幕');assert not app.exception
    go(app,'企画・ストーリー','ホーム・進行状況');assert not app.exception


def test_non_visible_video_does_not_inspect_or_render_sources(video_workspace,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Expensive inspection must only run explicitly')
    monkeypatch.setattr(VideoService,'inspect',forbidden)
    app=open_video()
    go(app,'企画・ストーリー','ホーム・進行状況')
    assert not app.exception
