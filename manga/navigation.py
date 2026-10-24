"""制作の6工程と遅延読み込み。旧画面名は内部リンクの互換用に解決する。"""
MENUS = {
    "企画・ストーリー": ["ホーム・進行状況", "プロジェクト管理", "生成・取り込み", "ページ編集", "ストーリー評価・改善", "比較・脚本確定", "参考作品", "参考作品の分析", "参考作品のAI候補", "ストーリー履歴", "参考作品の提案履歴"],
    "キャラクター・作風": ["AI一括生成・採用", "手動設定", "基準画像・作風", "制作提案の履歴"],
    "絵コンテ・作画指示": ["AI一括生成・採用", "コマ一覧・制作へ", "AI評価", "AI改善", "制作提案の履歴"],
    "イラスト制作": ["コマ制作ワークスペース", "画像プロンプト一括出力", "画像履歴"],
    "漫画ページ編集": [],
    "完成・書き出し": ["品質チェック・修正管理", "PNG・PDF書き出し", "音声制作・字幕", "動画制作・MP4出力", "API予算管理", "保存先・容量・診断", "GitHub作品同期"],
}


def resolve_route(menu, section=None):
    """保存済みリンクや以前の工程判定との互換性。表示するメニューは6つだけ。"""
    if menu in ("ホーム・進行状況", "プロジェクト管理"):
        return "企画・ストーリー", menu
    if menu == "ストーリー制作": return "企画・ストーリー", section
    if menu == "キャラクター設定": return "キャラクター・作風", section
    if menu == "作画指示・AI評価改善":
        return ("イラスト制作", "コマ制作ワークスペース") if section == "コマごとの編集" else ("絵コンテ・作画指示", section)
    if menu == "画像制作・画像管理":
        if section == "基準画像・作風": return "キャラクター・作風", section
        return "イラスト制作", "コマ制作ワークスペース" if section == "コマ画像・採用一覧" else section
    if menu == "PNG・PDF書き出し": return "完成・書き出し", "PNG・PDF書き出し"
    if menu == "履歴・設定":
        if section == "保存先・容量・診断": return "完成・書き出し", section
        if section == "ストーリー履歴": return "企画・ストーリー", section
        return "絵コンテ・作画指示", "制作提案の履歴"
    return menu, section


def dispatch(menu, section, project, store, navigate):
    if menu == "企画・ストーリー" and section == "ホーム・進行状況":
        from manga.home_ui import render_home
        render_home(project, store.directory, navigate)
    elif menu == "企画・ストーリー" and section == "プロジェクト管理":
        from manga.project_ui import render_project
        render_project(store)
    elif section == "API予算管理":
        from manga.budget_ui import render_budget
        render_budget(project, store.directory)
    elif section == "音声制作・字幕":
        from manga.voice_ui import render_voice
        render_voice(project,store.directory,navigate)
    elif section == "動画制作・MP4出力":
        from manga.video_ui import render_video
        render_video(project,store.directory)
    elif section == "保存先・容量・診断":
        from manga.settings_ui import render_settings
        render_settings(project, store.directory)
    elif section == "GitHub作品同期":
        from manga.github_sync_ui import render_github_sync
        render_github_sync(project, store.directory, navigate)
    elif project is None:
        from manga.ui_runtime import ui as st
        st.info("先にプロジェクトを作成するか、左の一覧から開いてください。")
    elif section in ("制作提案の履歴", "参考作品の提案履歴"):
        from manga.history_ui import render_production_history
        kinds = ("references",) if menu == "企画・ストーリー" else ("characters",) if menu == "キャラクター・作風" else ("directions", "evaluation", "improvement")
        render_production_history(project, store.directory, kinds)
    elif menu == "企画・ストーリー" and section != "参考作品のAI候補":
        from manga.story_ui import render_story_workspace
        render_story_workspace(project, store.directory, section)
    elif section == "品質チェック・修正管理":
        from manga.review_ui import render_review
        render_review(project, store.directory, navigate)
    elif menu in ("漫画ページ編集", "完成・書き出し"):
        from manga.page_ui import render_page_workspace
        render_page_workspace(project, store.directory, "editor" if menu == "漫画ページ編集" else "export")
    elif section in ("手動設定", "基準画像・作風"):
        from manga.artwork_ui import render_artwork_workspace
        render_artwork_workspace(project, store.directory, "characters" if section == "手動設定" else "references")
    elif menu == "イラスト制作" and section != "画像プロンプト一括出力":
        from manga.panel_workspace_ui import render_panel_workspace
        render_panel_workspace(project, store.directory, navigate, history_only=section == "画像履歴")
    elif section == "コマ一覧・制作へ":
        from manga.panel_workspace_ui import render_panel_overview
        render_panel_overview(project, store.directory, navigate)
    else:
        from manga.assistant_ui import render_assistant_workspace
        kind = "references" if menu == "企画・ストーリー" else "characters" if menu == "キャラクター・作風" else "images" if menu == "イラスト制作" else {"AI一括生成・採用":"directions", "AI評価":"evaluation", "AI改善":"improvement"}[section]
        render_assistant_workspace(project, store.directory, kind, navigate=navigate)


def stage_overview(rows):
    """保存データ由来の詳細な9作業を、利用者向けの6工程へまとめる。"""
    result = []
    for menu in MENUS:
        tasks = [r for r in rows if resolve_route(r["menu"], r["section"])[0] == menu]
        required = [r for r in tasks if not r["optional"]]
        states = {r["state"] for r in required}
        state = "要確認" if "要確認" in states else "完了" if states == {"完了"} else "進行中" if states & {"進行中", "完了"} else "未着手"
        result.append({"工程": menu, "状態": state, "確認内容": " / ".join(f"{r['label']}：{r['state']}" + ("（任意）" if r["optional"] else "") for r in tasks)})
    return result
