"""編集支援画面で共用する入力と保存結果の表示。"""

from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store

from manga.storage import StorageError
from manga.story import decode_story_file


def json_input(label, key):
    method = st.radio(f"{label}の入力方法", ["JSONファイル", "貼り付け"], horizontal=True, key=f"{key}_method")
    if method == "貼り付け":
        raw = st.text_area(f"{label}を貼り付け", height=200, key=f"{key}_raw")
        return raw, None, method
    upload = st.file_uploader(f"{label}ファイル", type=["json"], key=f"{key}_upload")
    return "", upload, method


def input_text(values):
    raw, upload, method = values
    if method == "JSONファイル":
        if upload is None:
            raise ValueError("取り込むJSONファイルを選択してください。")
        return decode_story_file(upload.getvalue())
    return raw


def perform(action, message, notice_key, selection_key=None):
    try:
        with operation("JSONの検証・保存を実行しています", invalidate_actions=True):
            result = action()
        if selection_key:
            st.session_state[f"{selection_key}_pending"] = None if result["deleted_at"] else result["id"]
        warnings = result[1] if isinstance(result, tuple) else []
        st.session_state[notice_key] = {"message": message, "warnings": warnings}
        st.rerun()
    except (ValueError, StorageError) as error:
        st.error(str(error))
