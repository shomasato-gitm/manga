"""保存済みの実体に基づくホーム。容量の再帰走査や画像読み込みは行わない。"""
from manga.ui_runtime import ui as st
from manga.ui_data import read_store, cached_read
from manga.artwork_storage import ArtworkStore
from manga.story_storage import StoryStore
from manga.page_storage import PageStore
from manga.assistant_storage import AssistantStore
from manga.studio_storage import StudioStore
from manga.studio_assets import asset_token
from manga.studio_workflow import progress
from manga.storage import StorageError


def render_home(project,directory,navigate):
    st.header("ホーム・進行状況")
    st.caption(f"保存先：{directory.resolve()}")
    if not project:
        st.info("プロジェクトを作成するか、左の一覧から開いてください。")
        if st.button("プロジェクトを作成する"):navigate("プロジェクト管理")
        return
    try:
        history=read_store(StoryStore(directory,project.id))
        art=read_store(ArtworkStore(directory,project.id))
        assistant_store=AssistantStore(directory,project.id)
        assistant=read_store(assistant_store)
        composition=read_store(PageStore(directory,project.id))
        studio_store=StudioStore(directory,project.id);studio=read_store(studio_store)
        rows,next_step,panels=progress(project,history,art,assistant,composition,studio,asset_token(directory/project.id,art,composition["settings"]),root=directory/project.id)
        if cached_read(assistant_store.journal_path,assistant_store.pending):
            st.warning("採用保存が途中です。新しい編集の前に再開してください。")
            next_step=dict(label="採用保存の再開",state="要確認",detail="保存途中の処理を完了してから制作を続けてください。",menu="キャラクター設定",section="AI一括生成・採用")
    except StorageError as error:
        st.error(str(error));return
    st.subheader("次にやること")
    if st.button('ストーリー制作スタジオを開く'):
        navigate('企画・ストーリー','ストーリー制作スタジオ')
    if next_step:
        st.info(f"{next_step['label']}：{next_step['state']}。{next_step['detail']}")
        if st.button("次の工程へ進む",type="primary"):navigate(next_step["menu"],next_step["section"])
    else:st.success("全工程の成果物がそろっています。書き出したファイルの保存を確認してください。")
    from manga.navigation import stage_overview
    st.dataframe(stage_overview(rows), use_container_width=True, hide_index=True)
    with st.expander("工程内の詳しい進行状況"):
        st.dataframe([{"作業":r["label"],"状態":r["state"],"確認内容":r["detail"]} for r in rows],use_container_width=True,hide_index=True)
    skip=st.checkbox("任意の作画評価をスキップする",value=studio["skip_evaluation"],key=f"skip_evaluation_{project.id}_{studio['revision']}")
    if skip != studio["skip_evaluation"]:
        studio_store.update(lambda d:d.update(skip_evaluation=skip));st.rerun()
    st.caption("参考作品登録・分析と作画評価は任意です。未実施でも次の必須工程へ進めます。完了は保存データと描画・出力の実績から判定します。")

    st.subheader("完成前の品質確認")
    st.caption("自動検査・目視レビュー・修正タスクは「完成・書き出し」で管理します。生成済みファイルがあっても、品質確認済みとは限りません。")
    if st.button("品質チェック・修正管理を開く"):
        navigate("完成・書き出し", "品質チェック・修正管理")
