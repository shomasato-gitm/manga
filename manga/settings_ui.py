"""保存先・容量・下書き・開発者向け診断。容量計測は明示時だけ。"""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from manga.ui_runtime import ui as st, operation
from manga.storage_usage import project_usage
from manga.ui_data import cache_stats, clear_cache


def render_settings(project,directory):
    st.header("保存先・容量・診断")
    st.code(str(directory.resolve()),language="text")
    st.info("ZIPでコードを更新する場合も、保存先をZIP展開フォルダの外へ置くとデータを保持しやすくなります。フォルダ全体をバックアップしてください。")
    st.code('export MANGA_DATA_DIR="$HOME/Documents/manga-projects"\npython -m streamlit run app.py --server.address 127.0.0.1',language="bash")
    st.caption("起動前に指定します。既存データは自動移動しません。アプリを終了して保存先フォルダ全体をコピーし、移動先を指定してください。")
    key=f"usage_{project.id}" if project else "usage_none"
    refresh=st.button("使用容量を再計測",disabled=project is None)
    if project and (key not in st.session_state or refresh):
        with operation("プロジェクトの使用容量を計測しています"):
            st.session_state[key]=dict(result=project_usage(directory,project.id),measured_at=datetime.now(ZoneInfo("Asia/Tokyo")).isoformat())
    if project:
        measured=st.session_state[key];usage=measured["result"]
        st.caption(f"計測日時（日本時間）：{measured['measured_at']}。画像保存後は再計測してください。")
        st.table([{"種類":label,"バイト数":usage[k],"MiB":round(usage[k]/1024/1024,3)} for k,label in (("json_bytes","JSON・履歴"),("image_bytes","画像"),("other_bytes","その他"),("total_bytes","合計"))])
        for warning in usage["warnings"]:st.warning(warning)
    st.subheader("入力の下書き")
    st.caption("入力内容はブラウザセッション内に保持します。ブラウザ終了・再起動で失われます。ファイル選択はメニュー復帰時に再選択が必要な場合があります。確定データは保存ボタンで保存してください。")
    if st.button("セッションの下書きを破棄（保存済みデータは保持）"):
        st.session_state["_input_drafts"]={}
        st.success("保持用の下書きを破棄しました。現在表示中のウィジェットは次の画面移動後に反映されます。")
    with st.expander("開発者向け診断"):
        st.json(cache_stats())
        st.dataframe(st.session_state.get("_screen_timings",[]),use_container_width=True)
        st.dataframe(st.session_state.get("_timings",[]),use_container_width=True)
        if st.button("表示用キャッシュをクリア"):
            clear_cache();st.session_state.pop("_page_previews",None);st.success("キャッシュをクリアしました。次回表示時に読み直します。")
        st.download_button("処理時間ログをJSONで取得",json.dumps(dict(screens=st.session_state.get("_screen_timings",[]),operations=st.session_state.get("_timings",[])),ensure_ascii=False,indent=2),file_name="manga_timings.json",mime="application/json")
