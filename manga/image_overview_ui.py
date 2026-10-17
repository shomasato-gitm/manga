"""画像制作の未完了コマを絞り込んで直接開く。"""
from manga.ui_runtime import ui as st
from manga.studio_workflow import panel_states


def image_overview(artwork, story, prefix, root=None):
    rows = panel_states(story,artwork,root)
    st.subheader("ページ・コマの制作状況")
    st.dataframe([{"ページ":r["page"],"コマ":r["panel"],"状態":r["status"],"確認":"要確認" if r["stale"] else ""} for r in rows], hide_index=True, use_container_width=True)
    unfinished = [r for r in rows if r["status"] != "採用済み" or r["stale"]]
    if unfinished:
        by_key={r["key"]:r for r in unfinished}
        target=st.selectbox("未完了コマへ移動",list(by_key), format_func=lambda k:f"{by_key[k]['page']}ページ・{by_key[k]['panel']}コマ / {by_key[k]['status']}",key=f"{prefix}_unfinished")
        if st.button("このコマの画像管理を開く",key=f"{prefix}_jump"):
            st.session_state[f"{prefix}_image_jump"] = by_key[target]
            st.rerun()
