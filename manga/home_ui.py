"""保存済みStoreから再構築する制作ダッシュボード。"""
from manga.ui_runtime import ui as st


def render_home(project,directory,navigate):
    st.caption('ホーム・進行状況')
    from manga.manga_mvp_ui import render_manga_mvp
    if render_manga_mvp(project,directory,navigate):
        if st.button('従来の制作工程・編集を開く'):
            navigate('漫画ページ編集')
        return
    if not project:
        st.info('プロジェクトを作成するか、左の一覧から開いてください。')
        if st.button('プロジェクトを作成する'): navigate('プロジェクト管理')
        return
    from manga.ux import header
    st.header(project.title)
    from manga.production_ui import render_dashboard
    render_dashboard(project,directory,navigate)
    from manga.automation_ui import render_automation
    with st.empty().container():
        render_automation(project,directory,navigate)

    st.subheader('最近の作業と成果物')
    last=st.session_state.get('_ux_last_screen',{}).get(project.id)
    if last:
        st.caption('最後に開いた作業：'+(last[1] or last[0]))
        if st.button('前回の作業を開く'): navigate(*last)
    from manga.studio_storage import StudioStore
    from manga.video_storage import VideoStore
    from manga.voice_storage import VoiceStore
    from manga.storage import StorageError
    try:
        saved=StudioStore(directory,project.id).load()['exports']
        saved=saved+VideoStore(directory,project.id).load()['exports']+VoiceStore(directory,project.id).load()['timelines']
        recent=sorted(saved,key=lambda e:e.get('saved_at') or e.get('created_at') or '',reverse=True)[:5]
        for event in recent:
            with st.container(border=True):
                st.write(event.get('filename') or event.get('kind') or '字幕・タイムライン')
                st.caption(event.get('saved_at') or event.get('created_at') or '')
        if not recent: st.caption('成果物はまだありません。上の「次にやること」から始めましょう。')
        from manga.artwork_storage import ArtworkStore
        art_store=ArtworkStore(directory,project.id); art=art_store.load()
        selected=[i for e in art['panels'].values() for i in e['candidates'] if i['id']==e['selected_image_id']]
        if selected and st.checkbox('代表画像を表示（採用画像の先頭）'):
            from manga.ux import thumbnail_card
            thumbnail_card(art_store,selected[0],'代表画像・品質確認は別途必要です')
    except (StorageError,OSError,ValueError) as error: st.error(str(error))
