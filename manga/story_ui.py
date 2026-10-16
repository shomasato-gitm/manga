"""ストーリーの手動受け渡し・編集画面。"""

import copy
import json

import streamlit as st

from manga.prompts import generation_prompt, improvement_prompt, story_template
from manga.storage import StorageError
from manga.story import decode_story_file, normalize_story, parse_story
from manga.story_storage import StoryStore


def render_story_workspace(project, directory):
    st.divider()
    st.header("ストーリー制作：ChatGPTと手動で連携")
    st.info("プロンプトをコピーして、ご自身でChatGPTへ貼り付けてください。返答をこの画面へ戻して保存します。API・ブラウザの自動操作は使いません。")
    st.caption("保存済みの漫画設定を使います。設定を変更したときは、先にプロジェクトを保存してください。")
    notice_key = f"story_notice_{project.id}"
    if notice_key in st.session_state:
        notice = st.session_state.pop(notice_key)
        st.success(notice["message"])
        for warning in notice.get("warnings", []):
            st.warning(warning)
    store = StoryStore(directory, project.id)
    try:
        history = store.load_history()
    except StorageError as error:
        st.error(str(error))
        return
    latest = history[-1] if history else None
    latest_id = latest["id"] if latest else None
    story = latest["story"] if latest else None
    if story and len(story["pages"]) != project.page_count:
        st.warning(f"保存済みストーリーは{len(story['pages'])}ページ、現在の設定は{project.page_count}ページです。再取り込み時は現在の設定に合わせてください。")

    prompt_tab, import_tab, edit_tab, history_tab = st.tabs(["ChatGPT用プロンプト", "JSON取り込み", "ページ編集", "バージョン履歴"])

    with prompt_tab:
        st.subheader("ストーリー生成用プロンプト")
        st.caption("下の枠の右上にあるコピーボタン（重なった四角）で全文をコピーできます。")
        st.code(generation_prompt(project), language="text", wrap_lines=True)
        st.download_button("JSONひな形をダウンロード", json.dumps(story_template(project), ensure_ascii=False, indent=2), file_name="story_template.json", mime="application/json", key=f"template_{project.id}")
        if story:
            st.subheader("評価・改善用プロンプト")
            request = st.text_area("改善してほしい点（任意）", max_chars=2000, key=f"improve_{project.id}")
            st.caption("最後に保存したストーリーが対象です。改善版も「JSON取り込み」から保存してください。")
            st.code(improvement_prompt(project, story, request), language="text", wrap_lines=True)

    with import_tab:
        st.subheader("ChatGPTの返答を取り込む")
        st.caption(f"全{project.page_count}ページ、各ページ約3コマ。1〜6コマを取り込めます。JSONは1MB以下・UTF-8形式です。")
        source = st.radio("取り込み方法", ["貼り付け", "JSONファイル"], horizontal=True, key=f"source_{project.id}")
        if source == "貼り付け":
            raw = st.text_area("ChatGPTのJSONを貼り付け", height=240, key=f"import_{project.id}")
            upload = None
        else:
            raw = ""
            upload = st.file_uploader("JSONファイルを選択", type=["json"], key=f"upload_{project.id}")
        if st.button("検証してストーリーを保存", key=f"import_button_{project.id}"):
            try:
                if source == "JSONファイル":
                    if upload is None:
                        raise ValueError("取り込むJSONファイルを選択してください。")
                    raw = decode_story_file(upload.getvalue())
                imported = parse_story(raw, project.page_count, project.title)
                store.save(imported.story, expected_version_id=latest_id)
                st.session_state[notice_key] = {"message": "ストーリーを取り込み、新しいバージョンとして保存しました。", "warnings": imported.warnings}
                st.rerun()
            except (ValueError, StorageError) as error:
                st.error(str(error))

    with edit_tab:
        if not story:
            st.info("JSONを取り込むと、ページごとに表示・編集できます。")
        else:
            _render_editor(project, store, latest, notice_key)

    with history_tab:
        if not history:
            st.info("保存するとバージョン履歴が作成されます。")
        else:
            labels = {version["id"]: f"v{index} ｜ {version['saved_at']} ｜ {version['action']}" for index, version in enumerate(history, 1)}
            selected_id = st.selectbox("復元するバージョン", list(reversed(labels)), format_func=labels.get, key=f"history_{project.id}")
            selected = next(version for version in history if version["id"] == selected_id)
            st.caption("日時はUTCです。復元しても以前の履歴は削除しません。編集中の内容は先に保存してください。")
            with st.expander("選択したバージョンの内容"):
                st.json(selected["story"])
            if st.button("このバージョンを復元", disabled=selected_id == latest_id, key=f"restore_{project.id}"):
                try:
                    store.restore(selected_id, expected_version_id=latest_id)
                    st.session_state[notice_key] = {"message": "選択したバージョンを復元し、新しい履歴として保存しました。"}
                    st.rerun()
                except (ValueError, StorageError) as error:
                    st.error(str(error))


def _render_editor(project, store, latest, notice_key):
    story = latest["story"]
    prefix = f"story_{project.id}_{latest['id']}"
    page_index = st.selectbox("編集するページ", list(range(len(story["pages"]))), format_func=lambda index: f"{index + 1}ページ", key=f"{prefix}_page")
    page = story["pages"][page_index]
    st.caption("ページの切り替え・取り込み・復元をする前に保存してください。保存していない編集内容は引き継がれません。")
    with st.form(f"{prefix}_{page_index}_form"):
        title = st.text_input("ストーリーのタイトル", value=story["title"], max_chars=100, key=f"{prefix}_{page_index}_title")
        summary = st.text_area("ストーリー全体のあらすじ", value=story["summary"], max_chars=4000, key=f"{prefix}_{page_index}_summary")
        page_summary = st.text_area("このページの概要", value=page["summary"], max_chars=2000, key=f"{prefix}_{page_index}_page_summary")
        edited_panels = []
        for panel in page["panels"]:
            panel_number = panel["panel"]
            key = f"{prefix}_{page_index}_{panel_number}"
            st.markdown(f"**{panel_number}コマ目**")
            edited_panels.append({
                "panel": panel_number,
                "description": st.text_area(f"{panel_number}コマ目の描写", value=panel["description"], max_chars=4000, key=f"{key}_description"),
                "dialogue": st.text_area(f"{panel_number}コマ目のセリフ", value=panel["dialogue"], max_chars=4000, key=f"{key}_dialogue"),
                "narration": st.text_area(f"{panel_number}コマ目のナレーション", value=panel["narration"], max_chars=2000, key=f"{key}_narration"),
            })
        submitted = st.form_submit_button("このページを保存")
    if submitted:
        try:
            edited = copy.deepcopy(story)
            edited.update(title=title, summary=summary)
            edited["pages"][page_index] = {"page": page_index + 1, "summary": page_summary, "panels": edited_panels}
            normalized = normalize_story(edited, len(story["pages"]))
            store.save(normalized.story, expected_version_id=latest["id"], action="ページ編集")
            st.session_state[notice_key] = {"message": f"{page_index + 1}ページ目を保存しました。", "warnings": normalized.warnings}
            st.rerun()
        except (ValueError, StorageError) as error:
            st.error(str(error))
    st.download_button("現在のストーリーJSONをダウンロード", json.dumps(story, ensure_ascii=False, indent=2), file_name=f"story_{project.id}.json", mime="application/json", key=f"export_{project.id}")
