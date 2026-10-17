"""各作業の配置を一箇所で定義する。非表示画面は呼び出さない。"""
MENUS = {
    "ホーム・進行状況": [], "プロジェクト管理": [],
    "ストーリー制作": ["生成・取り込み","ページ編集","ストーリー評価・改善","比較・脚本確定","参考作品","参考作品の分析","参考作品のAI候補"],
    "キャラクター設定": ["AI一括生成・採用","手動設定"],
    "作画指示・AI評価改善": ["AI一括生成・採用","コマごとの編集","AI評価","AI改善"],
    "画像制作・画像管理": ["コマ画像・採用一覧","基準画像・作風","画像プロンプト一括出力"],
    "漫画ページ編集": [], "PNG・PDF書き出し": [],
    "履歴・設定": ["ストーリー履歴","制作提案の履歴","保存先・容量・診断"],
}


def dispatch(menu,section,project,store,navigate):
    if menu=="ホーム・進行状況":
        from manga.home_ui import render_home
        render_home(project,store.directory,navigate)
    elif menu=="プロジェクト管理":
        from manga.project_ui import render_project
        render_project(store)
    elif menu=="履歴・設定" and section=="保存先・容量・診断":
        from manga.settings_ui import render_settings
        render_settings(project,store.directory)
    elif project is None:
        from manga.ui_runtime import ui as st
        st.info("先にプロジェクトを作成するか、左の一覧から開いてください。")
    elif menu=="ストーリー制作" and section!="参考作品のAI候補" or menu=="履歴・設定" and section=="ストーリー履歴":
        from manga.story_ui import render_story_workspace
        render_story_workspace(project,store.directory,section)
    elif menu=="漫画ページ編集" or menu=="PNG・PDF書き出し":
        from manga.page_ui import render_page_workspace
        render_page_workspace(project,store.directory,"editor" if menu=="漫画ページ編集" else "export")
    elif menu=="履歴・設定":
        from manga.history_ui import render_production_history
        render_production_history(project,store.directory)
    elif section in ("手動設定","コマごとの編集","コマ画像・採用一覧","基準画像・作風"):
        from manga.artwork_ui import render_artwork_workspace
        mode={"手動設定":"characters","コマごとの編集":"directions","コマ画像・採用一覧":"images","基準画像・作風":"references"}[section]
        render_artwork_workspace(project,store.directory,mode)
    else:
        from manga.assistant_ui import render_assistant_workspace
        kind="references" if menu=="ストーリー制作" else "characters" if menu=="キャラクター設定" else "images" if menu=="画像制作・画像管理" else {"AI一括生成・採用":"directions","AI評価":"evaluation","AI改善":"improvement"}[section]
        render_assistant_workspace(project,store.directory,kind)
