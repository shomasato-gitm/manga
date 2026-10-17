"""ストーリーの手動受け渡し・編集画面。"""

import copy
import json

from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store

from manga.prompts import generation_prompt, story_template
from manga.storage import StorageError
from manga.story import decode_story_file, normalize_story, parse_story
from manga.story_storage import StoryStore
from manga.artwork_storage import ArtworkStore
from manga.artwork_models import CHARACTER_FIELDS
from manga.editorial_storage import EditorialStore, generation_references
from manga.reference_ui import reference_management, reference_analysis
from manga.editorial_ui import render_workflow, evaluation_and_improvement, compare_and_finalize


def render_story_workspace(project, directory, section="生成・取り込み"):
    st.divider()
    st.header("ストーリー制作：ChatGPTと手動で連携")
    st.info("プロンプトをコピーして、ご自身でChatGPTへ貼り付けてください。返答をこの画面へ戻して保存します。API・ブラウザの自動操作は使いません。")
    st.caption("保存済みの漫画設定を使います。設定を変更したときは、先にプロジェクトを保存してください。")
    notice_key = f"story_notice_{project.id}"
    notice_slot=st.empty()
    if notice_key in st.session_state:
        notice=st.session_state.pop(notice_key)
        with notice_slot.container():
            st.success(notice["message"])
            for warning in notice.get("warnings",[]):st.warning(warning)
    store = StoryStore(directory, project.id)
    try:
        history = read_store(store)
    except StorageError as error:
        st.error(str(error))
        return
    latest = history[-1] if history else None
    latest_id = latest["id"] if latest else None
    story = latest["story"] if latest else None
    editorial_store = EditorialStore(directory, project.id)
    try:
        editorial = read_store(editorial_store) if section not in ("ページ編集","ストーリー履歴") else None
    except StorageError as error:
        editorial = None
        st.error(str(error))
    try:
        characters = []
        if section in ("生成・取り込み","ストーリー評価・改善"):
            artwork = read_store(ArtworkStore(directory, project.id))
            characters = [{key: item[key] for key in CHARACTER_FIELDS} for item in artwork["characters"] if not item["deleted_at"]]
    except StorageError:
        characters = None
    prefix = f"editorial_{project.id}"
    if editorial is not None and section == "比較・脚本確定":
        render_workflow(editorial, history)
    if story and len(story["pages"]) != project.page_count:
        st.warning(f"保存済みストーリーは{len(story['pages'])}ページ、現在の設定は{project.page_count}ページです。再取り込み時は現在の設定に合わせてください。")

    if section == "参考作品":
        if editorial is not None:
            reference_management(editorial_store, editorial, prefix, notice_key)
    if section == "参考作品の分析":
        if editorial is not None:
            reference_analysis(editorial_store, editorial, prefix, notice_key)

    if section == "生成・取り込み":
        st.subheader("ストーリー生成用プロンプト")
        st.caption("プロンプトは上部のコピー・ダウンロードから取得できます。全文の表示は任意です。")
        selected_references = []
        if editorial is not None:
            active_refs = {item["id"]: item for item in editorial["references"] if not item["deleted_at"]}
            selected_ids = st.multiselect("ストーリー生成に使う参考作品（任意・3作品まで）", list(active_refs), format_func=lambda value: active_refs[value]["title"], max_selections=3, key=f"{prefix}_generation_references")
            selected_references = generation_references(editorial, selected_ids)
        if characters is not None:
            from manga.prompt_ui import prompt_tools
            prompt_tools(generation_prompt(project, selected_references, characters), f"{prefix}_story_prompt", "story_prompt.txt")
        else:
            st.error("キャラクター設定が不正です。設定を維持するため、作画設定のエラーを解消してください。")
        st.download_button("JSONひな形をダウンロード", json.dumps(story_template(project), ensure_ascii=False, indent=2), file_name="story_template.json", mime="application/json", key=f"template_{project.id}")
        st.caption("評価と評価に基づく改善は「ストーリー制作 → ストーリー評価・改善」で行えます。参考作品を選ばなくても生成できます。")

    if section == "生成・取り込み":
        st.subheader("ChatGPTの返答を取り込む")
        st.caption(f"全{project.page_count}ページ、各ページ約3コマ。1〜6コマを取り込めます。JSONは1MB以下・UTF-8形式です。")
        source = st.radio("取り込み方法", ["JSONファイル", "貼り付け"], horizontal=True, key=f"source_{project.id}")
        if source == "貼り付け":
            raw = st.text_area("ChatGPTのJSONを貼り付け", height=240, key=f"import_{project.id}")
            upload = None
        else:
            raw = ""
            upload = st.file_uploader("JSONファイルを選択", type=["json"], key=f"upload_{project.id}")
        if st.button("検証してストーリーを保存", key=f"import_button_{project.id}"):
            try:
                with operation("ストーリーJSONを検証・保存しています", invalidate_actions=True):
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

    if section == "ページ編集":
        if not story:
            st.info("JSONを取り込むと、ページごとに表示・編集できます。")
        else:
            _render_editor(project, store, latest, notice_key)

    if section == "ストーリー履歴":
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
                    with operation("ストーリー履歴を復元しています", invalidate_actions=True):
                        store.restore(selected_id, expected_version_id=latest_id)
                    st.session_state[notice_key] = {"message": "選択したバージョンを復元し、新しい履歴として保存しました。"}
                    st.rerun()
                except (ValueError, StorageError) as error:
                    st.error(str(error))

    if section == "ストーリー評価・改善":
        if editorial is not None:
            evaluation_and_improvement(project, editorial_store, editorial, history, characters, prefix, notice_key)
    if section == "比較・脚本確定":
        if editorial is not None:
            compare_and_finalize(editorial_store, editorial, history, prefix, notice_key)


def _render_editor(project, store, latest, notice_key):
    story = latest["story"]
    prefix = f"story_{project.id}_{latest['id']}"
    page_index = st.selectbox("編集するページ", list(range(len(story["pages"]))), format_func=lambda index: f"{index + 1}ページ", key=f"{prefix}_page")
    page = story["pages"][page_index]
    st.caption("ページの切り替え・取り込み・復元をする前に保存してください。未保存の入力は同じ版・同じセッション内に保持します。版が変わる前に保存してください。")
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
            with operation("ストーリーの編集を保存しています", invalidate_actions=True):
                store.save(normalized.story, expected_version_id=latest["id"], action="ページ編集")
            st.session_state[notice_key] = {"message": f"{page_index + 1}ページ目を保存しました。", "warnings": normalized.warnings}
            st.rerun()
        except (ValueError, StorageError) as error:
            st.error(str(error))
    st.download_button("現在のストーリーJSONをダウンロード", json.dumps(story, ensure_ascii=False, indent=2), file_name=f"story_{project.id}.json", mime="application/json", key=f"export_{project.id}")
