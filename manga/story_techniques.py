"""参考作品の現行分析から、使用者が選んだ抽象技法だけを引き継ぐ。"""
import copy

from manga.editorial_storage import current_analysis, generation_references
from manga.models import validate_id


def technique_options(editorial, reference_ids):
    generated = generation_references(editorial, reference_ids)
    result, warnings = [], []
    for reference_id, abstract in zip(reference_ids, generated):
        reference = next(r for r in editorial['references'] if r['id'] == reference_id)
        analysis = current_analysis(editorial, reference)
        choices = []
        if reference['techniques']:
            choices.append(dict(id='registered', text=reference['techniques'],
                                status='登録メモ・未確認', basis='利用者が登録した抽象技法のメモ'))
        if analysis:
            for key, item in analysis['payload']['aspects'].items():
                if item['status'] == 'ユーザー要約に基づく' and item['abstract_technique']:
                    choices.append(dict(id=key, text=item['abstract_technique'],
                                        status=item['status'], basis=item['basis']))
        else:
            warnings.append(f"{reference['title']}：現行版の分析がありません。未確認の登録メモだけを使用できます。")
        result.append(dict(reference_id=reference_id, reference_version=reference['version'],
                           title=abstract['title'], avoid=abstract['avoid'],
                           analysis_id=analysis['id'] if analysis else None, techniques=choices))
    return result, warnings


def select_techniques(editorial, choices):
    options, warnings = technique_options(editorial, list(choices))
    selected = []
    for reference in options:
        ids = choices[reference['reference_id']]
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            raise ValueError('各参考作品で使用する技法を重複なく選択してください。')
        available = {t['id']: t for t in reference['techniques']}
        if not set(ids) <= set(available):
            raise ValueError('選択した技法は現行分析にありません。選択し直してください。')
        reference['techniques'] = [copy.deepcopy(available[key]) for key in ids]
        selected.append(reference)
    return selected, warnings


def stale_references(references, editorial):
    warnings = []
    for source in references:
        current = next((r for r in editorial['references'] if r['id'] == source['reference_id'] and not r['deleted_at']), None)
        analysis = current_analysis(editorial, current) if current else None
        if not current or current['version'] != source['reference_version'] or (analysis['id'] if analysis else None) != source['analysis_id']:
            warnings.append(f"{source['title']}：生成時の参考作品・分析は現在の登録内容と異なります。要確認です。")
    return warnings


def technique_keys(references):
    return {(r['reference_id'], t['id']) for r in references for t in r['techniques']}


def validate_references(references):
    from manga.editorial_models import text
    if not isinstance(references, list) or len(references) > 3:
        raise ValueError('参考作品は3作品までです。')
    seen = set()
    for ref in references:
        validate_id(ref['reference_id'])
        if ref['reference_id'] in seen or type(ref['reference_version']) is not int or ref['reference_version'] < 1:
            raise ValueError('参考作品ID・版が不正です。')
        seen.add(ref['reference_id'])
        if ref['analysis_id'] is not None:
            validate_id(ref['analysis_id'])
        text(ref['title'], '参考作品名', 2000, True)
        text(ref['avoid'], '避ける要素', 2000)
        if not isinstance(ref['techniques'], list) or not 1 <= len(ref['techniques']) <= 10:
            raise ValueError('使用技法は各作品1〜10件です。')
        ids = set()
        for item in ref['techniques']:
            key = text(item['id'], '技法ID', 40, True)
            if key in ids or item['status'] not in ('ユーザー要約に基づく', '登録メモ・未確認'):
                raise ValueError('技法ID・確認状態が不正です。')
            ids.add(key)
            text(item['text'], '抽象技法', 4000, True)
            text(item['basis'], '根拠', 4000, True)
