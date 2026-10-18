"""制作スタジオの回答契約。既存ストーリー・評価JSONは内側でそのまま扱う。"""
import json

from manga.editorial_models import obj, schema, text, normalize_targets, parse_evaluation
from manga.models import validate_id
from manga.story import load_json_payload, parse_story
from manga.story_comparison import check_improvement_scope
from manga.story_techniques import technique_keys

KINDS = ('concepts', 'detail', 'evaluation', 'improvement')
CONCEPT_FIELDS = {
    'title': 'タイトル', 'logline': 'ログライン', 'synopsis': '短いあらすじ',
    'protagonist_goal': '主人公の目的', 'conflict': '葛藤', 'turning_point': '転換点',
    'ending_direction': '結末の方向性', 'originality': '独自性', 'concerns': '懸念点',
}


def normalize_uses(values, references, *, review=False):
    if not isinstance(values, list) or len(values) > 30:
        raise ValueError('参考技法の活用情報は配列にしてください。')
    expected = technique_keys(references)
    seen, result = set(), []
    for raw in values:
        raw = obj(raw, '技法の活用')
        key = (raw.get('reference_id'), raw.get('technique_id'))
        if key not in expected or key in seen:
            raise ValueError('参考作品・技法IDが依頼と一致しないか重複しています。')
        seen.add(key)
        item = dict(reference_id=key[0], technique_id=key[1])
        if review:
            if raw.get('status') not in ('活用できている', '要改善', '未確認'):
                raise ValueError('技法の評価状態が不正です。')
            item.update(status=raw['status'], reason=text(raw.get('reason'), '活用の根拠', required=True))
        else:
            item['application'] = text(raw.get('application'), '技法の応用', required=True)
        result.append(item)
    if seen != expected:
        raise ValueError('依頼に含めたすべての参考技法の活用情報が必要です。')
    return result


def _normalize_response(raw, request):
    data = obj(load_json_payload(raw) if isinstance(raw, str) else raw, '制作スタジオの回答')
    schema(data)
    if data.get('request_id') != request['id'] or data.get('project_id') != request['project_id']:
        raise ValueError('request_id・project_idが選択中の依頼と一致しません。専用ひな形を使用してください。')
    kind = request['kind']
    result = dict(schema_version=1, request_id=request['id'], project_id=request['project_id'])
    references = request['context']['references']
    if kind == 'concepts':
        concepts = data.get('concepts')
        if not isinstance(concepts, list) or len(concepts) != 3:
            raise ValueError('概要案は1回の回答にちょうど3案必要です。')
        normalized = []
        for concept in concepts:
            concept = obj(concept, '概要案')
            item = {key: text(concept.get(key), label, 100 if key == 'title' else 2000, True)
                    for key, label in CONCEPT_FIELDS.items()}
            item['id'] = validate_id(concept.get('id'))
            cast = concept.get('main_characters')
            if not isinstance(cast, list) or not 1 <= len(cast) <= 20:
                raise ValueError('主要人物は1〜20人の配列にしてください。')
            item['main_characters'] = [dict(name=text(c.get('name'), '人物名', 100, True),
                                             role=text(c.get('role'), '人物の役割', 1000, True)) for c in cast]
            item['technique_uses'] = normalize_uses(concept.get('technique_uses'), references)
            normalized.append(item)
        if {c['id'] for c in normalized} != set(request['concept_ids']):
            raise ValueError('概要候補IDが依頼の3つのIDと一致しません。')
        for key in ('conflict', 'turning_point', 'ending_direction'):
            if len({c[key] for c in normalized}) != 3:
                raise ValueError('3案の葛藤・転換点・結末の方向性をそれぞれ異なる内容にしてください。')
        result['concepts'] = normalized
    elif kind in ('detail', 'improvement'):
        context = request['context']
        title = context['concept']['title'] if kind == 'detail' else context['source_story']['title']
        pages = context['project']['page_count'] if kind == 'detail' else len(context['source_story']['pages'])
        imported = parse_story(json.dumps(data.get('story'), ensure_ascii=False), pages, title)
        if kind == 'detail':
            if imported.story['title'] != title:
                raise ValueError('詳細脚本のタイトルを採用した概要案と一致させてください。')
            if [len(p['panels']) for p in imported.story['pages']] != context['panel_counts']:
                raise ValueError('ページごとのコマ数を依頼の設定と一致させてください。')
        else:
            check_improvement_scope(context['source_story'], imported.story,
                                    request['options']['mode'], request['options']['targets'])
        result['story'] = imported.story
    elif kind == 'evaluation':
        if data.get('story_version_id') != request['base_version_id']:
            raise ValueError('評価対象のストーリー版が依頼と一致しません。')
        result['story_version_id'] = request['base_version_id']
        result['evaluation'] = parse_evaluation(json.dumps(data.get('evaluation'), ensure_ascii=False),
                                                request['base_version_id'], request['context']['source_story'])
        result['reference_usage'] = normalize_uses(data.get('reference_usage'), references, review=True)
        contradictions = data.get('contradictions')
        if not isinstance(contradictions, list) or len(contradictions) > 100:
            raise ValueError('矛盾の指摘は100件以内の配列にしてください。問題がなければ空配列です。')
        result['contradictions'] = [dict(description=text(c.get('description'), '矛盾の内容', required=True),
                                         targets=normalize_targets(c.get('targets'), request['context']['source_story']))
                                    for c in contradictions]
    else:
        raise ValueError('制作依頼の種類が不正です。')
    return result


def normalize_response(raw, request):
    try:
        return _normalize_response(raw, request)
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
        raise ValueError('制作スタジオJSONの必須項目・型を確認してください。専用ひな形のID・配列を維持してください。') from error
