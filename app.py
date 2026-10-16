"""実行: python -m streamlit run app.py"""

import json
import os
from dataclasses import replace
from pathlib import Path

import streamlit as st

from manga.models import Project
from manga.storage import ProjectStore, StorageError
from manga.story_ui import render_story_workspace
from manga.artwork_ui import render_artwork_workspace


DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data" / "projects"


def set_form(project=None):
    """ウィジェットを作る前に値を設定して、読み込んだ内容をフォームへ反映する。"""
    st.session_state["active_project"] = project
    for name, default in (("title", ""), ("genre", ""), ("synopsis", ""), ("page_count", 8)):
        st.session_state[name] = getattr(project, name) if project else default


def main():
    st.set_page_config(page_title="AI漫画スタジオ", page_icon="📖", layout="centered")
    st.title("📖 AI漫画スタジオ")
    st.caption("漫画の企画・ストーリー・キャラクターと画像を、ChatGPTとの手動連携で管理します。")
    store = ProjectStore(Path(os.environ.get("MANGA_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser())
    if "active_project" not in st.session_state:
        set_form()

    with st.sidebar:
        st.header("保存済みプロジェクト")
        st.button("一覧を更新")
        try:
            projects, warnings = store.list_projects()
        except StorageError as error:
            projects, warnings = [], [str(error)]
        for warning in warnings:
            st.warning(warning)
        by_id = {project.id: project for project in projects}
        if projects:
            selection = st.selectbox(
                "プロジェクトを選択",
                options=list(by_id),
                format_func=lambda key: f"{by_id[key].title} / {by_id[key].genre} / {key[:8]}",
            )
            if st.button("選択したプロジェクトを開く"):
                try:
                    set_form(store.load(selection))
                    st.session_state["notice"] = "プロジェクトを読み込みました。"
                except StorageError as error:
                    st.error(str(error))
        else:
            st.info("保存済みプロジェクトはありません。")
        st.button("新規プロジェクト", on_click=set_form)
        st.caption("開く・新規作成を選ぶと、編集中の未保存内容は置き換わります。")

    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    active = st.session_state["active_project"]
    st.subheader("プロジェクトを編集" if active else "プロジェクトを作成")
    with st.form("project_form"):
        st.text_input("タイトル", key="title", max_chars=100)
        st.text_input("ジャンル", key="genre", max_chars=50, placeholder="例：ファンタジー、日常、SF")
        st.text_area("あらすじ", key="synopsis", max_chars=5000, height=180)
        st.number_input("ページ数", min_value=1, max_value=100, step=1, key="page_count")
        submitted = st.form_submit_button("プロジェクトを保存", type="primary")

    if submitted:
        try:
            fields = {name: st.session_state[name] for name in ("title", "genre", "synopsis", "page_count")}
            project = replace(active, **fields) if active else Project(**fields)
            st.session_state["active_project"] = store.save(project)
            st.session_state["notice"] = "プロジェクトを保存しました。"
            st.rerun()
        except (ValueError, StorageError) as error:
            st.error(str(error))

    if active:
        st.caption(f"ID: {active.id} ｜ 最終保存: {active.updated_at}")
        st.download_button(
            "保存済みJSONをダウンロード",
            data=json.dumps(active.to_dict(), ensure_ascii=False, indent=2),
            file_name=f"{active.id}.json",
            mime="application/json",
        )
        render_story_workspace(active, store.directory)
        render_artwork_workspace(active, store.directory)
    else:
        st.info("プロジェクトを保存すると、ストーリー制作を始められます。")
    st.caption("OpenAI APIは使用しません。APIキーは不要で、このアプリによるAPI課金は発生しません。")


if __name__ == "__main__":
    main()
