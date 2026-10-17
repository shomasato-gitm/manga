"""ページ単位のプロンプト表示。全件は明示的に生成する。"""
import json
from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store
from manga.artwork_storage import ArtworkStore
from manga.image_prompts import panel_image_prompt
from manga.assistant_prompts import batch_image_prompts
from manga.storage import StorageError


def render_image_prompts(project,directory):
    store=ArtworkStore(directory,project.id)
    try:
        art=read_store(store);history=read_store(store.story_store)
    except StorageError as error:
        st.error(str(error));return
    if not history:
        st.info("ストーリーと作画指示を保存してください。");return
    latest=history[-1];pages=latest["story"]["pages"]
    number=st.selectbox("プロンプトを表示するページ",list(range(1,len(pages)+1)),key=f"image_prompts_{project.id}_page")
    for panel in pages[number-1]["panels"]:
        st.subheader(f"{number}ページ・{panel['panel']}コマ")
        try:
            from manga.prompt_ui import prompt_tools
            prompt_tools(panel_image_prompt(project,latest["story"],pages[number-1],panel,art),
                         f"image_prompts_{project.id}_{latest['id']}_{number}_{panel['panel']}", f"image_p{number}_c{panel['panel']}.txt", response_json=False)
        except ValueError as error:st.warning(str(error))
    key=f"image_prompts_{project.id}_all"
    fingerprint=(latest["id"],art["revision"],json.dumps(project.to_dict(),ensure_ascii=False))
    if st.session_state.get(key,{}).get("fingerprint") != fingerprint:st.session_state.pop(key,None)
    if st.button("全画像プロンプトをまとめて作成",key=f"{key}_create") and key not in st.session_state:
        with operation("全コマの画像プロンプトを作成しています"):
            prompts,warnings,output=batch_image_prompts(project,latest,art)
            st.session_state[key]=dict(fingerprint=fingerprint,output=output,warnings=warnings,count=len(prompts))
    if key in st.session_state:
        result=st.session_state[key]
        st.caption(f"作成済み：{result['count']}コマ。参照画像はご自身でChatGPTへ添付してください。")
        for warning in result["warnings"]:st.warning(warning)
        st.download_button("全画像プロンプトをテキストでダウンロード",result["output"],file_name="manga_image_prompts.txt",mime="text/plain",key=f"{key}_download")
