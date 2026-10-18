"""ChatGPTへの手動依頼。回答の外側で依頼IDを照合し、脚本本体は既存形式。"""
import json
from dataclasses import replace

from manga.models import Project
from manga.prompts import generation_prompt
from manga.editorial_prompts import evaluation_prompt, evaluation_template
from manga.prompts import improvement_prompt
from manga.story_concept_models import CONCEPT_FIELDS


def response_template(request):
    result = dict(schema_version=1, request_id=request['id'], project_id=request['project_id'])
    refs = request['context']['references']
    uses = [dict(reference_id=r['reference_id'], technique_id=t['id'], application='この案への抽象的な応用方法')
            for r in refs for t in r['techniques']]
    if request['kind'] == 'concepts':
        result['concepts'] = [dict(id=identifier, **{key: label+'を記入' for key, label in CONCEPT_FIELDS.items()},
                                   main_characters=[dict(name='オリジナル人物', role='役割')], technique_uses=uses)
                              for identifier in request['concept_ids']]
    elif request['kind'] == 'evaluation':
        result.update(story_version_id=request['base_version_id'], evaluation=evaluation_template(request['base_version_id']),
                      reference_usage=[dict(reference_id=u['reference_id'], technique_id=u['technique_id'],
                                            status='未確認', reason='活用の根拠と改善点') for u in uses], contradictions=[])
    else:
        from manga.prompts import story_template
        context = request['context']
        if request['kind'] == 'detail':
            project = replace(Project.from_dict(context['project']), title=context['concept']['title'])
            story = story_template(project)
            for page, count in zip(story['pages'], context['panel_counts']):
                page['panels'] = [dict(panel=i+1, description='場面・動作を記入', dialogue='', narration='') for i in range(count)]
        else:
            story = context['source_story']
        result['story'] = story
    return result


def build_studio_prompt(request):
    context = request['context']
    project = Project.from_dict(context['project'])
    refs = context['references']
    common = (
        'ChatGPTへは利用者が手動で送信します。回答はJSONだけにしてください。\n'
        '参考にするのは抽象的な技法だけです。参考作品の人物・固有の場面・展開・セリフを再現せず、独立した創作にしてください。\n'
        '登録メモ・分析は検証済みの作品本文ではありません。未確認情報を事実と断定せず、不足は確認事項として示してください。\n'
        '以下の選択技法を全段階で維持してください。評価時はoriginalityとreference_usageで活用と独立性を確認してください。\n'
        '参考技法の生成時スナップショット：\n'+json.dumps(refs, ensure_ascii=False, indent=2)+'\n'
        '古い分析についての注意：\n'+json.dumps(request.get('warnings', []), ensure_ascii=False)+'\n'
        '生成元の採用概要（記録がある場合）：\n'+json.dumps(context.get('origin_concept'), ensure_ascii=False, indent=2)+'\n'
    )
    if request['kind'] == 'concepts':
        main = (
            'ストーリー概要を一度に3案提案してください。全案で中心的な葛藤・仕掛け（転換点）・結末の方向性を明確に変えてください。\n'
            '各案にタイトル、ログライン、短いあらすじ、主人公の目的、主要人物、葛藤、転換点、結末、技法の応用、独自性、懸念点を記載。\n'
            '選択した全技法について各案のtechnique_usesに使用方法を記入してください。設定不足はconcernsへ記載してください。\n'
            '作品設定：\n'+json.dumps(context['project'], ensure_ascii=False, indent=2)+'\n'
            '既存のオリジナル人物設定：\n'+json.dumps(context['characters'], ensure_ascii=False, indent=2)
        )
    elif request['kind'] == 'detail':
        main = '採用済みの概要を変更せず、全ページ・全コマの詳細脚本に展開してください。\n'
        main += '採用概要と技法の応用：\n'+json.dumps(context['concept'], ensure_ascii=False, indent=2)+'\n'
        main += generation_prompt(replace(project, title=context['concept']['title']), refs, context['characters'])
        main += '\nページごとのコマ数は次の配列を厳守（上記の約3コマより優先）：'+json.dumps(context['panel_counts'])
    elif request['kind'] == 'evaluation':
        main = evaluation_prompt(project, dict(id=request['base_version_id'], story=context['source_story']), context['characters'])
        main += '\n参考技法の活用とオリジナリティも確認。新たな矛盾・改善で失われた設定をcontradictionsへ対象とともに記載。判断できなければ未確認と明記。'
        if context.get('previous_story'):
            main += '\n改善前の脚本と評価・実際の変更箇所も比較し、得点の劣化や新たな矛盾を検討してください。\n'+json.dumps({k:context[k] for k in ('previous_version_id','previous_story','previous_evaluation','actual_changes')},ensure_ascii=False,indent=2)
    else:
        options = request['options']
        main = improvement_prompt(project, context['source_story'], options['reason'],
                                  evaluation=context['evaluation'], mode=options['mode'], criteria=options['criteria'],
                                  targets=options['targets'], characters=context['characters'])
        main += '\n元のテーマ・世界観・人物・採用概要の核と選択技法を維持し、改善による劣化や新たな矛盾を避けてください。'
    return common+main+'\n\n上記の出力指示より次の回答形式を優先してください。request_id・project_id・候補ID・脚本版を変更しないでください。'


def complete_prompt(request):
    return build_studio_prompt(request)+'\n脚本本体storyのschema_versionは1のまま、依頼IDは外側だけに置きます。\n'+json.dumps(response_template(request), ensure_ascii=False, indent=2)
