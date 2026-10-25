"""実行: python -m streamlit run app.py。選択した画面だけを実行する。"""
import os
import time
from pathlib import Path

from manga.ui_runtime import ui as st, scope, record_screen
from manga.ui_data import project_list
from manga.storage import ProjectStore, StorageError
from manga.navigation import MENUS, dispatch, resolve_route

DEFAULT_DATA_DIR=Path(__file__).resolve().parent/"data"/"projects"


def set_form(project=None):
    st.session_state["active_project"]=project
    for name,default in (("title",""),("genre",""),("synopsis",""),("page_count",8)):
        st.session_state[name]=getattr(project,name) if project else default


def navigate(menu,section=None):
    st.session_state["_navigation_pending"]=resolve_route(menu,section)
    st.rerun()


def main():
    st.set_page_config(page_title="AI漫画スタジオ",page_icon="📖",layout="wide")
    st.markdown('**AI漫画スタジオ**')
    st.markdown('<style>h1{font-size:2rem}h2{font-size:1.5rem}h3{font-size:1.15rem}@media(max-width:640px){h1{font-size:1.6rem}h2{font-size:1.3rem}}</style>',unsafe_allow_html=True)
    store=ProjectStore(Path(os.environ.get("MANGA_DATA_DIR",str(DEFAULT_DATA_DIR))).expanduser().resolve())
    if "active_project" not in st.session_state:set_form()
    with st.sidebar:
        st.header("保存済みプロジェクト")
        st.button("一覧を更新")
        try:projects,warnings=project_list(store)
        except StorageError as error:projects,warnings=[],[str(error)]
        for warning in warnings:st.warning(warning)
        by_id={p.id:p for p in projects}
        if by_id:
            selected=st.selectbox("プロジェクトを選択",list(by_id),format_func=lambda key:f"{by_id[key].title} / {by_id[key].genre} / {key[:8]}",key="sidebar_project")
            if st.button("選択したプロジェクトを開く"):
                try:
                    set_form(store.load(selected))
                    st.session_state["notice"]="プロジェクトを読み込みました。"
                    navigate("ホーム・進行状況")
                except StorageError as error:st.error(str(error))
        if st.button("新規プロジェクト"):
            set_form();navigate("プロジェクト管理")
        pending=st.session_state.pop("_navigation_pending",None)
        if pending:
            menu,section=pending
            st.session_state["navigation"]=menu
            if section:st.session_state[f"route_{menu}"]=section
        from manga.ux import PRIMARY, detailed, group
        st.radio('表示モード',('標準モード','詳細モード'),key='ux_mode',horizontal=True)
        st.subheader('制作ナビゲーション')
        active_menu=st.session_state.get('navigation','企画・ストーリー')
        active_section=st.session_state.get(f'route_{active_menu}','ホーム・進行状況' if st.session_state.get('active_project') else 'プロジェクト管理')
        active_group=group(active_menu,active_section)
        for label,(target_menu,target_section) in PRIMARY.items():
            if st.button(label,key='ux_nav_'+label,use_container_width=True,type='primary' if label==active_group else 'secondary'): navigate(target_menu,target_section)
        with st.expander('従来の工程・すべての作業',expanded=detailed()):
            menu=st.radio("制作メニュー",list(MENUS),index=0,key="navigation")
        st.caption("入力後は欄外をクリックしてから画面を切り替えてください。下書きはセッション内に保持し、確定には保存・採用ボタンを押してください。")
    notice=st.empty()
    if "notice" in st.session_state:notice.success(st.session_state.pop("notice"))
    project=st.session_state["active_project"]
    if project:
        from manga.ux import details
        with st.sidebar:
            with details('作品の詳細情報'): st.caption('作品UUID：'+project.id)
    started=time.perf_counter()
    section=None
    with scope(f"{project.id if project else 'new'}:{menu}"):
        with st.sidebar:
            with st.empty().container():
                if MENUS[menu]:
                    if menu == '企画・ストーリー' and f'route_{menu}' not in st.session_state:
                        st.session_state[f'route_{menu}'] = 'ホーム・進行状況' if project else 'プロジェクト管理'
                    from manga.ux import detailed
                    with st.expander('作業を切り替える・全機能',expanded=detailed()):
                        section=st.selectbox('作業を選択',MENUS[menu],key=f'route_{menu}')
                else:
                    st.caption('漫画編集：ページを選んで編集できます。')
        from manga.ux import shell
        shell(menu,section,project,navigate)
        with scope(f"{project.id if project else 'new'}:{menu}:{section}"):
            return_area = st.empty()
            if st.session_state.get("_review_return"):
                with return_area.container():
                    from manga.review_ui_common import return_link
                    return_link(project,menu,section,navigate)
            # 長い品質画面と短い出力画面の切替で旧ウィジェットを残さない固定領域。
            quality_area, dashboard_area, manual_area, production_area = st.empty(), st.empty(), st.empty(), st.empty()
            area = quality_area if section == '品質チェック・修正管理' else dashboard_area if section == 'ホーム・進行状況' else manual_area if section == '手動画像制作' else production_area
            with area.container():
                dispatch(menu,section,project,store,navigate)
    record_screen(f"{menu} / {section or ''}",time.perf_counter()-started)
    st.caption("ChatGPTとの連携は手動です。APIキー・OpenAI API・Web画面の自動操作は使用しません。")


if __name__=="__main__":main()
