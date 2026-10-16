"""ユーザーがChatGPTへ貼り付ける文章を組み立てる。"""

import json

from manga.models import Project


def story_template(project: Project) -> dict:
    return {
        "schema_version": 1,
        "title": project.title,
        "summary": "物語全体のあらすじを記入",
        "pages": [
            {
                "page": page,
                "summary": "このページの出来事を記入",
                "panels": [
                    {"panel": panel, "description": "このコマの場面・動作・表情を記入", "dialogue": "", "narration": ""}
                    for panel in range(1, 4)
                ],
            }
            for page in range(1, project.page_count + 1)
        ],
    }


def _settings(project: Project) -> str:
    return json.dumps({"タイトル": project.title, "ジャンル": project.genre, "あらすじ": project.synopsis, "ページ数": project.page_count}, ensure_ascii=False, indent=2)


def _format_rules(project: Project) -> str:
    return (
        f"全{project.page_count}ページ、各ページ3コマを目安にしてください。"
        "pageとpanelは配列順に1からの連番にし、ページの省略をしないでください。\n"
        "起承転結、主人公の目的、読みやすい場面転換、人物の一貫性を意識してください。\n"
        "descriptionには絵にできる場面・動作・表情を記入し、dialogueは話者を含む文字列、"
        "narrationは文字列にしてください。セリフのないコマは空文字列にしてください。\n"
        "schema_versionは1。titleは100文字以内、summaryは4000文字以内、ページのsummaryは2000文字以内、"
        "各コマのdescriptionとdialogueは4000文字以内、narrationは2000文字以内にしてください。\n"
        "全ページを含む有効なJSONにし、コメント、末尾の余分なカンマ、NaN、プレースホルダーを含めないでください。\n"
    )


def generation_prompt(project: Project) -> str:
    return (
        "あなたは日本語の漫画脚本家です。次の漫画設定から、コマ単位のストーリーを作成してください。\n\n"
        f"漫画設定：\n{_settings(project)}\n\n{_format_rules(project)}\n"
        "次のJSONひな形の記入用の文を実際の内容にすべて置き換え、JSONだけを出力してください。\n"
        + json.dumps(story_template(project), ensure_ascii=False, indent=2)
    )


def improvement_prompt(project: Project, story: dict, request: str = "") -> str:
    return (
        "あなたは日本語の漫画編集者です。漫画設定と現在のストーリーを評価し、改善してください。\n"
        "評価観点は、導入の魅力、主人公の動機、起承転結、各ページの引き、コマ間の連続性、"
        "人物と設定の一貫性、セリフの長さ、結末の納得感です。\n"
        "最初に良い点・問題点・改善方針を短い日本語で述べ、その後に改善版の全ページを"
        "1つの```jsonコードブロックで出力してください。差分や省略記法ではなく全体を返してください。\n"
        "元のJSONと同じ項目を使い、評価文をJSON内に追加しないでください。\n\n"
        f"漫画設定：\n{_settings(project)}\n\n{_format_rules(project)}\n"
        f"追加の改善希望：\n{request.strip() or '特になし。評価に基づいて改善してください。'}\n\n"
        "現在のストーリー：\n" + json.dumps(story, ensure_ascii=False, indent=2)
    )
