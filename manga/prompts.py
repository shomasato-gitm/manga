"""ユーザーがChatGPTへ貼り付ける文章を組み立てる。"""

import json
from dataclasses import replace

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


def generation_prompt(project: Project, references=(), characters=()) -> str:
    if len(references) > 3:
        raise ValueError("参考作品は3作品まで選択してください。")
    reference_text = ""
    if characters:
        reference_text += "オリジナルの登録キャラクター設定を維持してください：\n" + json.dumps(list(characters), ensure_ascii=False, indent=2) + "\n"
    if references:
        reference_text += (
            "参考作品から抽象化した技法：\n" + json.dumps(list(references), ensure_ascii=False, indent=2)
            + "\n参考にするのは抽象的なストーリー技法・演出方法だけです。オリジナルの登場人物・世界観・設定にし、"
            "既存作品の固有の展開・場面・セリフを再現しないでください。参考にしたくない要素を避けてください。\n\n"
        )
    return (
        "あなたは日本語の漫画脚本家です。次の漫画設定から、コマ単位のストーリーを作成してください。\n\n"
        f"漫画設定：\n{_settings(project)}\n\n{reference_text}{_format_rules(project)}\n"
        "次のJSONひな形の記入用の文を実際の内容にすべて置き換え、JSONだけを出力してください。\n"
        + json.dumps(story_template(project), ensure_ascii=False, indent=2)
    )


def improvement_prompt(project: Project, story: dict, request: str = "", *, evaluation=None,
                       mode="全体を改善する", criteria=(), targets=(), characters=()) -> str:
    from manga.editorial_models import CRITERIA, MODES

    if mode not in MODES:
        raise ValueError("改善方式が不正です。")
    scope = ""
    if evaluation is not None:
        selected = {key: evaluation["criteria"][key] for key in criteria}
        scope = (
            f"改善方式：{mode}\n重点項目：{json.dumps([CRITERIA[key] for key in criteria], ensure_ascii=False)}\n"
            f"対象ページ・コマ：{json.dumps(list(targets), ensure_ascii=False)}\n"
            + "保存済み評価のうち改善対象の内容：\n" + json.dumps(selected, ensure_ascii=False, indent=2) + "\n"
        )
    if mode == MODES[1]:
        scope += "指定ページ・コマだけを改善し、範囲外の内容、タイトル、全体のあらすじを一字も変更しないでください。\n"
    elif mode == MODES[2]:
        scope += "選んだ評価項目の問題点を重点的に改善し、関係のない改変を避けてください。\n"
    constraints = (
        "元のテーマ・世界観・登場人物の設定と外見を維持し、別作品の固有の展開やセリフを再現しないでください。\n"
        + "登録キャラクター設定：\n" + json.dumps(list(characters), ensure_ascii=False, indent=2) + "\n"
    )
    return (
        "あなたは日本語の漫画編集者です。漫画設定と現在のストーリーを評価し、改善してください。\n"
        "評価観点は、導入の魅力、主人公の動機、起承転結、各ページの引き、コマ間の連続性、"
        "人物と設定の一貫性、セリフの長さ、結末の納得感です。\n"
        "改善版の全ページを含むストーリーJSONだけを出力してください。差分や省略記法ではなく全体を返してください。\n"
        "元のJSONと同じ項目を使い、評価文をJSON内に追加しないでください。\n\n"
        f"漫画設定：\n{_settings(replace(project, page_count=len(story['pages'])))}\n\n{_format_rules(replace(project, page_count=len(story['pages'])))}\n"
        + constraints + scope
        + f"追加の改善希望：\n{request.strip() or '特になし。評価に基づいて改善してください。'}\n\n"
        "現在のストーリー：\n" + json.dumps(story, ensure_ascii=False, indent=2)
    )
