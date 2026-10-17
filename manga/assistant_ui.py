"""ChatGPTとの手動連携をまとめたAI制作アシスタント画面。"""

import json

import streamlit as st

from manga.assistant_evaluation_ui import evaluation_report, evaluations
from manga.assistant_models import KINDS, targets_for
from manga.assistant_prompts import batch_image_prompts, response_template
from manga.assistant_review_ui import review_proposal
from manga.assistant_storage import AssistantStore
from manga.assistant_samples import sample_response
from manga.editorial_models import SUBJECTIVE_NOTICE
from manga.editorial_ui_common import input_text, json_input
from manga.storage import StorageError


IMPORT_LABELS = {"characters": "キャラクター提案JSON", "directions": "作画指示提案JSON", "references": "参考作品候補JSON", "evaluation": "作画評価JSON", "improvement": "作画改善JSON"}


def _select(label, records, key, format_func):
    ids = [item["id"] for item in records]
    if f"{key}_pending" in st.session_state:
        st.session_state[key] = st.session_state.pop(f"{key}_pending")
    if st.session_state.get(key) not in ids:
        st.session_state[key] = ids[-1]
    return st.selectbox(label, list(reversed(ids)), format_func=format_func, key=key)


def _scope(story, prefix):
    mode = st.selectbox("出力対象", ("全コマ", "指定ページ", "指定コマ"), key=f"{prefix}_scope")
    page, panel = None, None
    page_slot, panel_slot = st.empty(), st.empty()
    if mode != "全コマ":
        pages = [item["page"] for item in story["pages"]]
        if st.session_state.get(f"{prefix}_page") not in pages:
            st.session_state[f"{prefix}_page"] = pages[0]
        page = page_slot.selectbox("対象ページ", pages, key=f"{prefix}_page")
    if mode == "指定コマ":
        panels = [item["panel"] for item in story["pages"][page - 1]["panels"]]
        if st.session_state.get(f"{prefix}_panel") not in panels:
            st.session_state[f"{prefix}_panel"] = panels[0]
        panel = panel_slot.selectbox("対象コマ", panels, key=f"{prefix}_panel")
    return targets_for(story, page, panel)


def _workspace(project, store, data, latest, artwork, editorial, kind, theme, prefix, run):
    # 保存後に提案フォームが消える場合も、旧フォームを残さない。
    with st.empty().container():
        _workspace_content(project, store, data, latest, artwork, editorial, kind, theme, prefix, run)


def _workspace_content(project, store, data, latest, artwork, editorial, kind, theme, prefix, run):
    key = f"{prefix}_{kind}"
    targets, evaluation_id = [], None
    if kind in ("directions", "evaluation", "improvement"):
        targets = _scope(latest["story"], key)
    if kind == "improvement":
        records = evaluations(data)
        if not records:
            st.info("作画評価JSONを取り込むと、評価をもとにした改善依頼を作れます。")
            return
        by_id = {item["id"]: item for item in records}
        evaluation_id = st.selectbox("改善に使う作画評価", list(reversed(by_id)), format_func=lambda value, records=by_id: f"{records[value]['saved_at']} / {value[:8]}", key=f"{key}_evaluation")
        with st.expander("改善元の評価内容"):
            st.json(by_id[evaluation_id]["payload"])
    if kind == "references":
        st.caption("作品情報は要確認として取り込みます。外部サイトへのアクセス・スクレイピングは行いません。")
    if st.button(f"{KINDS[kind]}の依頼を作成", key=f"{key}_create"):
        run(lambda: store.create_request(project, kind, targets=targets, theme=theme, evaluation_id=evaluation_id, expected_revision=data["revision"]),
            "生成依頼を保存しました。プロンプトをChatGPTへ手動で渡してください。", f"{key}_request")
    requests = [item for item in data["requests"] if item["kind"] == kind]
    if not requests:
        st.info("未作成：依頼を作成すると、返答JSONの形式を含むプロンプトを表示します。")
        return
    by_id = {item["id"]: item for item in requests}
    request_id = _select("表示する生成依頼", requests, f"{key}_request", lambda value: f"{by_id[value]['saved_at']} / {value[:8]}")
    request = by_id[request_id]
    stale = request["story_version_id"] != latest["id"]
    stale_slot = st.empty()
    if stale:
        stale_slot.warning("この依頼は古いストーリー版です。履歴の閲覧はできますが、取り込み・採用には最新の依頼が必要です。")
    with st.expander("ChatGPTへ渡すプロンプト", expanded=True):
        st.code(request["prompt"], language="text", wrap_lines=True)
        st.caption("枠右上のコピーを使ってChatGPTへ貼り付けてください。送信は自動では行いません。")
        st.download_button("依頼プロンプトをダウンロード", request["prompt"], file_name=f"assistant_{kind}_{request_id[:8]}.txt", mime="text/plain", key=f"{key}_prompt_download")
    with st.expander("返答形式を確認"):
        st.json(response_template(request))
        st.download_button("動作確認用の返答サンプルJSON", json.dumps(sample_response(request), ensure_ascii=False, indent=2), file_name=f"sample_{kind}.json", mime="application/json", key=f"{key}_sample")
        st.caption("サンプルは実際のAI返答ではありません。現在の依頼IDに対応するため、そのまま取り込んで差分確認まで試せます。採用する場合は内容を修正してください。")
    values = json_input(IMPORT_LABELS[kind], f"{key}_{request_id}_import")
    if st.button(f"{IMPORT_LABELS[kind]}を検証して取り込む", disabled=stale, key=f"{key}_import_button"):
        run(lambda: store.import_proposal(request_id, input_text(values), expected_revision=data["revision"]),
            "提案を別保存しました。既存設定は変更していません。内容を確認してください。", f"{key}_{request_id}_proposal")
    proposals = [item for item in data["proposals"] if item["request_id"] == request_id]
    proposal_area = st.empty()
    if not proposals:
        proposal_area.info("提案待ち：ChatGPTの返答JSONを貼り付けるか、UTF-8のJSONファイルを取り込んでください。")
        return
    with proposal_area.container():
        pmap = {item["id"]: item for item in proposals}
        proposal_id = _select("表示する提案履歴", proposals, f"{key}_{request_id}_proposal", lambda value: f"{pmap[value]['saved_at']} / {value[:8]}")
        proposal = pmap[proposal_id]
        if kind == "evaluation":
            evaluation_report(data, proposal, f"{key}_{proposal_id}")
        else:
            review_proposal(store, data, proposal, request, artwork, editorial, f"{key}_{proposal_id}", run)


def _progress(data):
    rows = []
    for kind, label in KINDS.items():
        requests = [item for item in data["requests"] if item["kind"] == kind]
        proposals = [item for item in data["proposals"] if requests and item["request_id"] == requests[-1]["id"]]
        state = "未作成" if not requests else "提案待ち"
        if proposals:
            decisions = list(proposals[-1]["decisions"].values())
            state = "評価保存済み" if kind == "evaluation" else "確認待ち" if "確認待ち" in decisions else "採用済み" if "採用済み" in decisions else "見送り"
        rows.append({"支援機能": label, "状態（最新依頼）": state})
    st.table(rows)


def render_assistant_workspace(project, directory):
    st.divider()
    st.header("AI制作アシスタント")
    st.caption("依頼作成 → ChatGPTへ手動コピー → JSON取り込み → 差分確認・編集 → 選択した提案だけ採用。APIキー・API接続は不要です。")
    store = AssistantStore(directory, project.id)
    prefix = f"assistant_{project.id}"
    notice_key = f"{prefix}_notice"
    notice_slot = st.empty()
    if notice_key in st.session_state:
        notice_slot.success(st.session_state.pop(notice_key))

    def run(action, message, selection_key=None):
        try:
            result = action()
            if selection_key:
                st.session_state[f"{selection_key}_pending"] = result["id"]
            st.session_state[notice_key] = message
            st.rerun()
        except (ValueError, StorageError) as error:
            st.error(str(error))
            if store.journal_path.exists():
                st.caption("採用保存が途中の場合は、画面を更新して「採用保存を再開」を押してください。")

    try:
        data = store.load()
        if store.pending():
            st.warning("採用保存が途中で停止しました。変更前後のJSONは再開記録に残っています。再開が完了するまで他の編集を避けてください。")
            if st.button("採用保存を再開", key=f"{prefix}_resume"):
                run(store.resume, "採用保存を完了しました。")
            return
        latest = store.latest()
        artwork, editorial = store.artwork.load(), store.editorial.load()
    except StorageError as error:
        st.error(str(error))
        return
    except ValueError as error:
        st.info(str(error))
        return
    theme = st.text_input("制作テーマ・補足（任意、不明なら空欄）", max_chars=2000, key=f"{prefix}_theme")
    _progress(data)
    tabs = st.tabs(["A：キャラクター一括生成", "B：参考作品候補", "C：作画指示一括生成", "D：作画評価・改善", "E：画像プロンプト一括出力"])
    for tab, kind in zip(tabs[:3], ("characters", "references", "directions")):
        with tab:
            _workspace(project, store, data, latest, artwork, editorial, kind, theme, prefix, run)
    with tabs[3]:
        st.caption(SUBJECTIVE_NOTICE)
        st.subheader("作画を評価する")
        _workspace(project, store, data, latest, artwork, editorial, "evaluation", theme, prefix, run)
        st.subheader("評価をもとに改善する")
        _workspace(project, store, data, latest, artwork, editorial, "improvement", theme, prefix, run)
    with tabs[4]:
        prompts, warnings, output = batch_image_prompts(project, latest, artwork)
        st.caption("各枠の右上から1コマずつコピーできます。記載された参照画像はご自身でChatGPTへ添付してください。")
        if warnings:
            st.warning(f"画像プロンプトを作れないコマが{len(warnings)}件あります。未設定・変更済みの指示を確認してください。")
            with st.expander("不足情報・確認事項の一覧"):
                st.table([{"対象と必要な操作": warning} for warning in warnings])
        for item in prompts:
            with st.expander(f"{item['page']}ページ・{item['panel']}コマの画像プロンプト"):
                st.code(item["prompt"], language="text", wrap_lines=True)
        if prompts:
            st.download_button("全画像プロンプトをテキストでダウンロード", output, file_name="manga_image_prompts.txt", mime="text/plain", key=f"{prefix}_image_download")
        else:
            st.info("採用・保存済みの作画指示がありません。Cで提案を採用するか、既存の作画画面で保存してください。")
