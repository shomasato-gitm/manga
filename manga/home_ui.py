"""保存済みStoreから再構築する制作ダッシュボード。"""
from manga.ui_runtime import ui as st


def render_home(project,directory,navigate):
    st.header('ホーム・進行状況')
    st.caption(f'保存先：{directory.resolve()}')
    if not project:
        st.info('プロジェクトを作成するか、左の一覧から開いてください。')
        if st.button('プロジェクトを作成する'): navigate('プロジェクト管理')
        return
    from manga.production_ui import render_dashboard
    render_dashboard(project,directory,navigate)
    from manga.automation_ui import render_automation
    with st.empty().container():
        render_automation(project,directory,navigate)
