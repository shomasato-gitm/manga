"""分析・評価用の貼り付けプロンプトとJSONひな形。外部アクセスはしない。"""

import json

from manga.editorial_models import ANALYSIS_ASPECTS, CRITERIA, REFERENCE_FIELDS, SUBJECTIVE_NOTICE
from manga.prompts import _settings


def analysis_template(reference_id, reference_version=1):
    return {"schema_version": 1, "reference_id": reference_id, "reference_version": reference_version, "aspects": {
        key: {"analysis": "不明。必要な情報を説明", "abstract_technique": "", "basis": "不明", "status": "不明"}
        for key in ANALYSIS_ASPECTS
    }, "unknowns": "追加で必要な情報"}


def analysis_prompt(reference):
    settings = {label: reference[key] for key, label in REFERENCE_FIELDS.items()}
    return (
        "日本語の物語分析者として、ユーザーが登録した要約からストーリー技法を分析してください。\n"
        "本文や固有のセリフ・場面を大量に複製せず、抽象的な技法・演出方法を中心にしてください。\n"
        "登録情報はユーザーの要約・メモであり、確認済みの作品本文ではありません。URLも未検証の出典メモです。\n"
        "情報が不足する場合は不明と明記し、追加で必要な情報をunknownsへ記入してください。"
        "実際に確認していない場面や展開を事実として断定せず、知っているつもりの内容で補完しないでください。\n"
        "statusは「ユーザー要約に基づく」「未確認」「不明」のいずれか。basisには登録要約の根拠や未確認であることを示してください。\n"
        "analysis、abstract_technique、basis、unknownsは各4000文字以内。次の9項目をすべて分析してください。\n"
        + json.dumps(ANALYSIS_ASPECTS, ensure_ascii=False, indent=2)
        + "\n\n登録情報：\n" + json.dumps(settings, ensure_ascii=False, indent=2)
        + "\n\n下の形式でJSONだけを返してください。reference_idとreference_versionを変更しないでください。\n"
        + json.dumps(analysis_template(reference["id"], reference["version"]), ensure_ascii=False, indent=2)
    )


def evaluation_template(version_id):
    return {"schema_version": 1, "story_version_id": version_id, "criteria": {
        key: {"score": 5, "good_points": "良い点", "problems": "問題点。なければなし",
              "reason": "改善理由。不要ならその理由", "targets": [{"page": 1, "panel": 1}], "suggestion": "具体的な改善案"}
        for key in CRITERIA
    }, "summary": "総評"}


def evaluation_prompt(project, version, characters=()):
    return (
        "あなたは日本語の漫画編集者です。次の現在のストーリーを評価し、評価JSONだけを返してください。\n"
        + SUBJECTIVE_NOTICE + "\n"
        "8項目をそれぞれ0〜10点で採点し、根拠を具体的に説明してください。総合点はアプリが合計÷80×100で計算します。\n"
        "good_points、problems、reason、suggestionは各4000文字以内。問題がない場合も「なし」等を明記してください。\n"
        "targetsは実在するページ・コマだけを指定。ページ全体はpanel:null、全体の指摘は空配列[]です。"
        "想像したコマや参考作品との未確認の一致を根拠にしないでください。脚本をここで改変しないでください。\n"
        + json.dumps(CRITERIA, ensure_ascii=False, indent=2)
        + f"\n漫画設定：\n{_settings(project)}\n登録キャラクター設定：\n{json.dumps(list(characters), ensure_ascii=False, indent=2)}"
        + "\n現在のストーリー：\n" + json.dumps(version["story"], ensure_ascii=False, indent=2)
        + "\n\n次のJSON形式の各値を評価結果に置き換え、story_version_idは変更しないでください。\n"
        + json.dumps(evaluation_template(version["id"]), ensure_ascii=False, indent=2)
    )
