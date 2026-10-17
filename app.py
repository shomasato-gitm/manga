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
    st.title("📖 AI漫画スタジオ")
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
        menu=st.radio("制作メニュー",list(MENUS),index=0,key="navigation")
        st.caption("入力後は欄外をクリックしてから画面を切り替えてください。下書きはセッション内に保持し、確定には保存・採用ボタンを押してください。")
    notice=st.empty()
    if "notice" in st.session_state:notice.success(st.session_state.pop("notice"))
    project=st.session_state["active_project"]
    if project:st.caption(f"現在のプロジェクト：{project.title} ｜ ID：{project.id}")
    started=time.perf_counter()
    section=None
    with scope(f"{project.id if project else 'new'}:{menu}"):
        if MENUS[menu]:
            if menu == "企画・ストーリー" and f"route_{menu}" not in st.session_state:
                st.session_state[f"route_{menu}"] = "ホーム・進行状況" if project else "プロジェクト管理"
            section=st.selectbox("作業を選択",MENUS[menu],key=f"route_{menu}")
        with scope(f"{project.id if project else 'new'}:{menu}:{section}"):
            dispatch(menu,section,project,store,navigate)
    record_screen(f"{menu} / {section or ''}",time.perf_counter()-started)
    st.caption("ChatGPTとの連携は手動です。APIキー・OpenAI API・Web画面の自動操作は使用しません。")


if __name__=="__main__":main()
