"""人物採用の復旧記録にも、既存画像・人物・他の提案の保持を要求する。"""
from manga.artwork_models import CHARACTER_ALL_FIELDS, character_fields
from manga.storage import StorageError


def validate_recovery(journal):
    before, after = journal['before_assistant'], journal['after_assistant']
    if not after['adoptions'] or not after['adoptions'][-1].get('character_ai'):
        raise StorageError('従来の採用保存が途中です。既存のAI一括生成・採用画面で再開してください。')
    try:
        if journal['target'] != 'artwork' or after['revision'] != before['revision'] + 1 or after['requests'] != before['requests'] or after['adoptions'][:-1] != before['adoptions']:
            raise ValueError
        adoption = after['adoptions'][-1]
        if len(adoption['changes']) != 1: raise ValueError
        change = adoption['changes'][0]
        old, new = journal['before_target'], journal['after_target']
        if {k:v for k,v in old.items() if k not in ('revision', 'characters')} != {k:v for k,v in new.items() if k not in ('revision', 'characters')} or new['revision'] != old['revision'] + 1:
            raise ValueError
        affected = change['result_id']
        if [c for c in old['characters'] if c['id'] != affected] != [c for c in new['characters'] if c['id'] != affected]: raise ValueError
        target = next(c for c in new['characters'] if c['id'] == affected)
        previous = next((c for c in old['characters'] if c['id'] == affected), None)
        if character_fields(target) != change['after']: raise ValueError
        if previous:
            if {k:v for k,v in previous.items() if k not in (*character_fields(previous), 'updated_at')} != {k:v for k,v in target.items() if k not in (*character_fields(target), 'updated_at')}: raise ValueError
            if {k:previous.get(k, '') for k in CHARACTER_ALL_FIELDS} != change['before']: raise ValueError
        elif target['references'] or target['primary_reference_id'] is not None or target['deleted_at'] is not None:
            raise ValueError
        old_proposals = {p['id']:p for p in before['proposals']}
        if set(old_proposals) != {p['id'] for p in after['proposals']}: raise ValueError
        for proposal in after['proposals']:
            original = old_proposals[proposal['id']]
            if proposal['id'] != adoption['proposal_id']:
                if proposal != original: raise ValueError
            else:
                if proposal['original_payload'] != original['original_payload'] or proposal['edits'][:-1] != original['edits']: raise ValueError
                if {k:v for k,v in proposal['decisions'].items() if k != change['item_id']} != {k:v for k,v in original['decisions'].items() if k != change['item_id']}: raise ValueError
                if [i for i in proposal['payload']['items'] if i['id'] != change['item_id']] != [i for i in original['payload']['items'] if i['id'] != change['item_id']]: raise ValueError
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        raise StorageError('人物採用の再開記録が保持条件に一致しません。既存データを変更せず停止しました。') from error
