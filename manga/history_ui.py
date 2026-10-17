"""履歴を選んで閲覧する。採用・編集は元の作業画面へ一本化する。"""
from manga.ui_runtime import ui as st
from manga.ui_data import read_store
from manga.assistant_storage import AssistantStore
from manga.assistant_models import KINDS
from manga.storage import StorageError


def render_production_history(project,directory):
    store=AssistantStore(directory,project.id)
    try:data=read_store(store)
    except StorageError as error:st.error(str(error));return
    by_id={r["id"]:r for r in data["requests"]}
    if not by_id:st.info("制作提案の履歴はまだありません。");return
    selected=st.selectbox("履歴の生成依頼",list(reversed(by_id)),format_func=lambda key:f"{KINDS[by_id[key]['kind']]} / {by_id[key]['saved_at']}",key=f"history_assistant_{project.id}")
    request=by_id[selected]
    st.caption(f"依頼ID：{selected} ｜ ストーリー版：{request['story_version_id']}")
    proposals=[p for p in data["proposals"] if p["request_id"]==selected]
    st.table([{"提案ID":p["id"],"保存日時":p["saved_at"],"採用済み":list(p["decisions"].values()).count("採用済み"),"確認待ち":list(p["decisions"].values()).count("確認待ち")} for p in proposals])
    if proposals:
        by_proposal={p["id"]:p for p in proposals}
        p_id=st.selectbox("履歴の提案",list(by_proposal),key=f"history_proposal_{project.id}_{selected}")
        if st.checkbox("選んだ提案の詳細を表示",key=f"history_details_{p_id}"):
            st.json(dict(proposal=by_proposal[p_id],adoptions=[a for a in data["adoptions"] if a["proposal_id"]==p_id]))
    st.caption("確認・編集・採用はキャラクター、作画指示、参考作品のそれぞれの作業画面で行ってください。履歴は削除しません。")
