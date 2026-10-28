"""既存のプロジェクト作成・保存を専用画面で再利用する。"""
import json
from dataclasses import replace
from manga.ui_runtime import ui as st, operation
from manga.models import Project
from manga.storage import StorageError


def render_project(store):
    if st.button('テーマから新しい漫画をまとめて制作'):
        from app import navigate
        navigate('ホーム・進行状況')
    active=st.session_state.get("active_project")
    st.subheader("プロジェクトを編集" if active else "プロジェクトを作成")
    with st.form("project_form"):
        st.text_input("タイトル", value=active.title if active else "", key="title", max_chars=100)
        st.text_input("ジャンル", value=active.genre if active else "", key="genre", max_chars=50, placeholder="例：ファンタジー、日常、SF")
        st.text_area("あらすじ", value=active.synopsis if active else "", key="synopsis", max_chars=5000, height=180)
        st.number_input("ページ数", value=active.page_count if active else 8, min_value=1, max_value=100, step=1, key="page_count")
        submitted = st.form_submit_button("プロジェクトを保存", type="primary")

    if submitted:
        try:
            fields = {name: st.session_state[name] for name in ("title", "genre", "synopsis", "page_count")}
            project = replace(active, **fields) if active else Project(**fields)
            with operation("プロジェクトを保存しています", invalidate_actions=True):
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
