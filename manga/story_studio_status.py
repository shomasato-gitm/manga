"""版の系譜と実際の差分・点数から制作状況を算出する。AIの品質保証はしない。"""
from manga.story_comparison import compare_stories
from manga.editorial_models import CRITERIA


def selected_concept(data):
    selection = next((r for r in data['selections'] if r['id'] == data['selected_id']), None)
    if not selection:
        return None, None, None
    batch = next(b for b in data['batches'] if b['id'] == selection['batch_id'])
    concept = next(c for c in batch['payload']['concepts'] if c['id'] == selection['concept_id'])
    request = next(r for r in data['requests'] if r['id'] == batch['request_id'])
    return selection, concept, request


def lineage(data, history, version_id):
    by_id = {v['id']: v for v in history}
    details = {r['version_id']: r for r in data['details']}
    version, cycles, visited = by_id.get(version_id), 0, set()
    root, origin = version_id, None
    while version and version['id'] not in visited:
        visited.add(version['id']); root = version['id']
        if root in details:
            origin = details[root]
            break
        revision = version.get('revision_context')
        if revision:
            cycles += 1
        parent = (version.get('restored_from') or (revision or {}).get('base_version_id')
                  or (version.get('parent_version_id') if version['action']=='ページ編集' else None))
        version = by_id.get(parent)
    return dict(root_version_id=root, cycles=cycles, origin=origin)


def references_for_version(data, history, version_id):
    origin = lineage(data, history, version_id)['origin']
    if not origin:
        return []
    request = next(r for r in data['requests'] if r['id'] == origin['request_id'])
    return request['context']['references']


def exact_evaluation(editorial, version):
    return next((e for e in reversed(editorial['evaluations'])
                 if e['payload']['story_version_id'] == version['id'] and e['source_story'] == version['story']), None)


def cycle_limit(data, root):
    approved = [r['limit'] for r in data['extensions'] if r['root_version_id'] == root]
    return max([data['policy']['improvement_limit']]+approved)


def score_rows(editorial, history):
    rows = []
    for index, version in enumerate(history, 1):
        evaluation = exact_evaluation(editorial, version)
        row = dict(版=f'v{index}', version_id=version['id'], 総合点=evaluation['payload']['total_score'] if evaluation else None)
        for key, label in CRITERIA.items():
            row[label] = evaluation['payload']['criteria'][key]['score'] if evaluation else None
        rows.append(row)
    return rows


def regression_notes(data, editorial, history, version):
    result = []
    evaluation = exact_evaluation(editorial, version)
    parent_id = (version.get('revision_context') or {}).get('base_version_id')
    parent = next((v for v in history if v['id'] == parent_id), None)
    previous = exact_evaluation(editorial, parent) if parent else None
    if evaluation and previous:
        for key, label in CRITERIA.items():
            before, after = previous['payload']['criteria'][key]['score'], evaluation['payload']['criteria'][key]['score']
            if after < before:
                result.append(f'{label}が低下：{before:g} → {after:g}')
    linked = next((e for e in reversed(data['evaluations']) if evaluation and e['evaluation_id'] == evaluation['id']), None)
    if linked:
        old_link=next((e for e in reversed(data['evaluations']) if previous and e['evaluation_id']==previous['id']),None)
        old_problems={c['description'] for c in old_link['contradictions']} if old_link else set()
        result += [('AIが指摘した新たな矛盾（要確認）：' if c['description'] not in old_problems else 'AIが引き続き指摘する矛盾（要確認）：')+c['description'] for c in linked['contradictions']]
        result += ['参考技法の活用が要確認：'+u['reason'] for u in linked['reference_usage'] if u['status'] != '活用できている']
    return result


def stage(data, editorial, history):
    selection, concept, _ = selected_concept(data)
    latest = history[-1] if history else None
    if not selection:
        return '概要3案・選択', '概要3案を作成し、比較して1案を採用してください。'
    if not any(d['selection_id'] == selection['id'] for d in data['details']):
        return '詳細脚本', '採用した概要から全ページの詳細脚本を作成してください。'
    if not latest or not exact_evaluation(editorial, latest):
        return '評価・改善', '最新の脚本を評価してください。改善後・復元後は再評価が必要です。'
    confirmed = any(f['story_version_id'] == latest['id'] and f['source_story'] == latest['story'] for f in editorial['finalizations'])
    if confirmed:
        return '比較・確定', '脚本は確定済みです。キャラクター・作画工程へ進めます。'
    evaluation = exact_evaluation(editorial, latest)
    if evaluation['payload']['total_score'] >= data['policy']['target_score']:
        return '比較・確定', '目標点に達しました。変更箇所・独自性を人間が最終確認してください。'
    chain = lineage(data, history, latest['id'])
    if chain['cycles'] >= cycle_limit(data, chain['root_version_id']):
        return '比較・確定', '改善回数の目安に達しました。追加改善の承認、または残る課題を確認して確定を選べます。'
    return '評価・改善', '最新の評価から改善し、新しい版を再評価してください。'


def actual_changes(before, after):
    return compare_stories(before['story'], after['story'])
