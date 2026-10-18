"""概要・依頼・制作履歴の読み取りと検証。書き込みはStudioServiceで一括保全する。"""
import json
from datetime import datetime
from pathlib import Path

from manga.models import Project, validate_id
from manga.storage import StorageError
from manga.story_concept_models import KINDS, normalize_response
from manga.story_techniques import validate_references
from manga.story import normalize_story
from manga.artwork_models import character_fields
from manga.editorial_models import MODES, CRITERIA, normalize_targets, normalize_evaluation, text

COLLECTIONS = ('requests', 'batches', 'selections', 'details', 'evaluations',
               'improvements', 'extensions', 'finalizations', 'responses', 'events')


def empty_concepts(project_id):
    return dict(schema_version=1, project_id=project_id, revision=0, selected_id=None, reference_choices={},
                policy=dict(target_score=85, improvement_limit=3), **{key: [] for key in COLLECTIONS})


def validate_concepts(data, project_id):
    if type(data['schema_version']) is not int or data['schema_version'] != 1 or data['project_id'] != project_id:
        raise ValueError('概要履歴の作品ID・形式が不正です。')
    if type(data['revision']) is not int or data['revision'] < 0:
        raise ValueError('概要履歴の版が不正です。')
    policy = data['policy']
    choices = data.get('reference_choices', {})
    if not isinstance(choices, dict) or len(choices) > 3:
        raise ValueError('参考技法の選択は3作品までです。')
    for identifier, keys in choices.items():
        validate_id(identifier)
        if not isinstance(keys, list) or not keys or len(keys) != len(set(keys)) or not all(isinstance(k, str) for k in keys):
            raise ValueError('保存された技法の選択が不正です。')
    if type(policy['target_score']) is not int or not 0 <= policy['target_score'] <= 100 or type(policy['improvement_limit']) is not int or not 1 <= policy['improvement_limit'] <= 100:
        raise ValueError('目標点は0〜100、改善目安は1〜100で指定してください。')
    seen = set()
    for key in COLLECTIONS:
        if not isinstance(data[key], list):
            raise ValueError('制作履歴は配列にしてください。')
        for row in data[key]:
            validate_id(row['id'])
            if row['id'] in seen or datetime.fromisoformat(row['saved_at']).tzinfo is None:
                raise ValueError('制作履歴のID・日時が不正です。')
            seen.add(row['id'])
    requests = {r['id']: r for r in data['requests']}
    for request in requests.values():
        if request['project_id'] != project_id or request['kind'] not in KINDS or not isinstance(request['prompt'], str):
            raise ValueError('制作依頼の形式が不正です。')
        if request['base_version_id'] is not None:
            validate_id(request['base_version_id'])
        if Project.from_dict(request['context']['project']).id != project_id:
            raise ValueError('制作設定の作品IDが不正です。')
        context = request['context']
        validate_references(context['references'])
        counts = context['panel_counts']
        if not isinstance(counts,list) or len(counts)!=context['project']['page_count'] or any(type(n) is not int or not 1<=n<=6 for n in counts):
            raise ValueError('詳細脚本のページ・コマ数が不正です。')
        if not isinstance(context['characters'],list) or len(context['characters'])>100:
            raise ValueError('人物設定の形式が不正です。')
        cast_ids=set()
        for character in context['characters']:
            validate_id(character['id'])
            if character['id'] in cast_ids or any(character.get(k)!=v for k,v in character_fields(character).items()):
                raise ValueError('人物ID・設定が不正です。')
            cast_ids.add(character['id'])
        if request['kind'] == 'concepts':
            if len(request['concept_ids']) != 3 or len(set(request['concept_ids'])) != 3:
                raise ValueError('概要候補IDは3つ必要です。')
            for identifier in request['concept_ids']:
                validate_id(identifier)
        elif request['kind'] in ('evaluation','improvement'):
            validate_id(request['base_version_id'])
            if normalize_story(context['source_story']).story!=context['source_story']:
                raise ValueError('依頼の元脚本が不正です。')
            if request['kind']=='improvement':
                validate_id(request['evaluation_id'])
                options=request['options']
                if options['mode'] not in MODES or not isinstance(options['criteria'],list) or not options['criteria'] or not set(options['criteria'])<=set(CRITERIA):
                    raise ValueError('改善依頼の方式・項目が不正です。')
                normalize_targets(options['targets'],context['source_story'],required=options['mode']==MODES[1])
                text(options['reason'],'改善理由',required=True)
                if normalize_evaluation(context['evaluation'],request['base_version_id'],context['source_story'])!=context['evaluation']:
                    raise ValueError('改善依頼の元評価が不正です。')
    batches = {}
    for batch in data['batches']:
        request = requests[batch['request_id']]
        if request['kind'] != 'concepts' or normalize_response(batch['payload'], request) != batch['payload']:
            raise ValueError('概要候補の形式が不正です。')
        batches[batch['id']] = batch
    selections = {r['id']: r for r in data['selections']}
    for row in selections.values():
        if row['concept_id'] not in {c['id'] for c in batches[row['batch_id']]['payload']['concepts']}:
            raise ValueError('採用する概要候補がありません。')
    if data['selected_id'] is not None and data['selected_id'] not in selections:
        raise ValueError('採用状態が概要履歴と一致しません。')
    for request in requests.values():
        if request['kind']=='detail':
            selection=selections[request['selection_id']]
            batch=batches[selection['batch_id']]
            concept=next(c for c in batch['payload']['concepts'] if c['id']==selection['concept_id'])
            source=requests[batch['request_id']]
            if request['context']['concept']!=concept or request['context']['references']!=source['context']['references']:
                raise ValueError('詳細依頼と採用概要・参考技法が一致しません。')
    for row in data['details']:
        if requests[row['request_id']]['kind'] != 'detail' or row['selection_id'] not in selections or row['selection_id']!=requests[row['request_id']]['selection_id']:
            raise ValueError('詳細脚本の生成元が不正です。')
        validate_id(row['version_id'])
    for key in ('evaluations', 'improvements', 'finalizations'):
        for row in data[key]:
            validate_id(row['version_id'])
            validate_id(row['evaluation_id'])
            if key != 'finalizations' and row['request_id'] not in requests:
                raise ValueError('評価・改善の依頼がありません。')
            if key=='evaluations':
                request=requests[row['request_id']]
                if request['kind']!='evaluation' or request['base_version_id']!=row['version_id']:
                    raise ValueError('評価と対象版が一致しません。')
                from manga.story_concept_models import normalize_uses
                if normalize_uses(row['reference_usage'],request['context']['references'],review=True)!=row['reference_usage']:
                    raise ValueError('技法の活用評価が不正です。')
                if not isinstance(row['contradictions'],list):raise ValueError('矛盾の指摘が不正です。')
                for item in row['contradictions']:
                    text(item['description'],'矛盾の指摘',required=True)
                    normalize_targets(item['targets'],request['context']['source_story'])
            if key=='improvements' and (requests[row['request_id']]['kind']!='improvement' or row['evaluation_id']!=requests[row['request_id']]['evaluation_id']):
                raise ValueError('改善と対象評価が一致しません。')
            if key=='finalizations' and (row['reviewed'] is not True or type(row['accept_remaining']) is not bool):
                raise ValueError('脚本の最終確認状態が不正です。')
    for row in data['extensions']:
        validate_id(row['root_version_id'])
        if type(row['limit']) is not int or not 1 <= row['limit'] <= 100 or not isinstance(row['reason'], str) or not row['reason'].strip():
            raise ValueError('追加改善の承認が不正です。')
    for row in data['responses']:
        request = requests[row['request_id']]
        if row['status'] not in ('確認待ち', '保存済み') or normalize_response(row['payload'], request) != row['payload']:
            raise ValueError('受信した回答の形式が不正です。')
    return data


class ConceptStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.path = Path(directory) / project_id / 'story_concepts.json'

    def load(self):
        try:
            return validate_concepts(json.loads(self.path.read_text(encoding='utf-8')), self.project_id)
        except FileNotFoundError:
            return empty_concepts(self.project_id)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError('概要・制作履歴を読み込めません。上書きせずバックアップを確認してください。') from error
