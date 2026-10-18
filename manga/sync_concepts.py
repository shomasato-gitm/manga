"""概要とPDCAは明示採用時だけ履歴を追加する。同じIDの異なる内容は停止する。"""
import copy

from manga.story_concept_storage import COLLECTIONS, empty_concepts, validate_concepts
from manga.story_studio import record
from manga.editorial_storage import EditorialStore
from manga.sync_manifest import decode_json, json_bytes, SyncConflict


def check_story_links(data, history):
    by_id = {v['id']:v for v in history}
    ids = set(by_id)
    for collection in ('details', 'evaluations', 'improvements', 'finalizations'):
        if any(r['version_id'] not in ids for r in data[collection]):
            raise ValueError('概要・PDCAに対応するストーリー履歴がありません。対応する脚本も採用してください。')
    for row in data['requests']:
        if row['base_version_id'] is not None and row['base_version_id'] not in ids:
            raise ValueError('制作依頼の元ストーリー版がありません。対応する履歴を確認してください。')
        if row['kind'] in ('evaluation','improvement') and row['context']['source_story']!=by_id[row['base_version_id']]['story']:
            raise ValueError('制作依頼の元脚本が履歴の本文と一致しません。')
        if row['context'].get('previous_version_id'):
            previous=by_id.get(row['context']['previous_version_id'])
            if not previous or previous['story']!=row['context']['previous_story']:
                raise ValueError('再評価の比較元脚本が履歴と一致しません。')
    requests={r['id']:r for r in data['requests']}
    for row in data['details']:
        context=requests[row['request_id']]['context'];story=by_id[row['version_id']]['story']
        if story['title']!=context['concept']['title'] or [len(p['panels']) for p in story['pages']]!=context['panel_counts']:
            raise ValueError('詳細脚本と概要・コマ数の対応が一致しません。')
    from manga.story_comparison import compare_stories
    for row in data['improvements']:
        version=by_id[row['version_id']];base=by_id.get(row['base_version_id'])
        context=version.get('revision_context',{})
        if not base or context.get('base_version_id')!=row['base_version_id'] or context.get('evaluation_id')!=row['evaluation_id'] or context.get('reason')!=row['reason'] or row['changes']!=compare_stories(base['story'],version['story']):
            raise ValueError('改善履歴が脚本の実際の変更・理由と一致しません。')
    if any(r['root_version_id'] not in ids for r in data['extensions']):
        raise ValueError('追加改善を承認した元の脚本版がありません。')


def check_editorial_links(data, editorial):
    evaluations = {row['id']:row for row in editorial['evaluations']}
    analyses = {row['id']:row for row in editorial['analyses']}
    references = {row['id']:row for row in editorial['references']}
    for kind in ('evaluations','improvements','finalizations'):
        for row in data[kind]:
            evaluation=evaluations.get(row['evaluation_id'])
            expected=row['base_version_id'] if kind=='improvements' else row['version_id']
            if not evaluation or evaluation['payload']['story_version_id']!=expected:
                raise ValueError('制作PDCAに対応する評価履歴がありません。概要・脚本・参考分析・評価を合わせて同期してください。')
    for request in data['requests']:
        for source in request['context']['references']:
            reference=references.get(source['reference_id'])
            analysis=analyses.get(source['analysis_id']) if source['analysis_id'] else None
            if not reference or reference['version']<source['reference_version']:
                raise ValueError('生成元の参考作品の版がありません。')
            if source['analysis_id'] and (not analysis or analysis['payload']['reference_id']!=source['reference_id'] or analysis['payload']['reference_version']!=source['reference_version']):
                raise ValueError('生成元の参考分析が一致しません。')


def append_history(existing, incoming, label):
    known = {r['id']: r for r in existing}
    for row in incoming:
        old = known.get(row['id'])
        if old is not None and old != row:
            raise SyncConflict(f'{label}の同じIDに異なる内容があります。既存履歴の上書きを停止しました。')
        if old is None:
            existing.append(copy.deepcopy(row)); known[row['id']] = row


def concept_adoption(directory, project_id, current, incoming, future_history):
    if 'story_concepts.json' not in incoming:
        raise ValueError('同期候補に概要・制作PDCAがありません。')
    source = incoming['story_concepts.json']
    data = decode_json(current['story_concepts.json']) if 'story_concepts.json' in current else empty_concepts(project_id)
    for key in COLLECTIONS:
        append_history(data[key], source[key], '概要・制作'+key)
    data['events'].append(record(kind='sync', previous_selected_id=data['selected_id'], previous_policy=copy.deepcopy(data['policy'])))
    data['selected_id'] = source['selected_id']
    data['policy'] = copy.deepcopy(source['policy'])
    data['reference_choices'] = copy.deepcopy(source.get('reference_choices', {}))
    data['revision'] += 1
    validate_concepts(data, project_id)
    check_story_links(data, future_history)
    result = {'story_concepts.json':json_bytes(data)}
    if 'editorial.json' in incoming:
        target = decode_json(current['editorial.json']) if 'editorial.json' in current else EditorialStore(directory, project_id).load()
        for key in ('references', 'analyses', 'evaluations'):
            append_history(target[key], incoming['editorial.json'][key], '参考作品・評価'+key)
        # 別端末の確定を現在の端末での最終確認にしない。元の確定履歴は候補内に保持。
        target['revision'] += 1
        EditorialStore(directory, project_id).validate(target)
        result['editorial.json'] = json_bytes(target)
    target_editorial=decode_json(result['editorial.json']) if 'editorial.json' in result else (decode_json(current['editorial.json']) if 'editorial.json' in current else EditorialStore(directory,project_id).load())
    check_editorial_links(data,target_editorial)
    return result
