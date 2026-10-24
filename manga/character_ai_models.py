"""人物の正本を複製せず、確認済みバイブルと検査の独立履歴を検証する。"""
from datetime import datetime

from manga.artwork_models import CHARACTER_ALL_FIELDS, character_fields, text
from manga.assistant_models import validate_targets
from manga.models import validate_id
from manga.story import normalize_story

FIXED_FIELDS = {k: CHARACTER_ALL_FIELDS[k] for k in ('age', 'hairstyle', 'hair_color', 'eye_color', 'body_type', 'clothing', 'features', 'height', 'visual_identity')}
VARIABLE_ELEMENTS = ('表情', '姿勢', '照明')
STATIC_NOTICE = 'これはテキスト設定・人物ID・版・ハッシュ・承認状況の静的検査です。画像の顔や外見の一致は解析・保証していません。'


def empty(project_id):
    return dict(schema_version=1, format='manga-character-ai', project_id=project_id, revision=0,
                bibles=[], observations=[], checks=[], prompts=[], events=[])


def normalize_exceptions(values, story):
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError('場面例外は100件までです。')
    result = []
    for row in values:
        if not isinstance(row, dict) or set(row) != {'targets', 'changes', 'reason'}:
            raise ValueError('例外にはtargets・changes・reasonが必要です。')
        changes = normalize_features(row['changes'])
        if not changes:
            raise ValueError('場面例外の変更項目を指定してください。')
        targets = validate_targets(row['targets'], story)
        reason = text(row['reason'], '場面例外の理由', 2000, True)
        for previous in result:
            overlap = {(t['page'], t['panel']) for t in previous['targets']} & {(t['page'], t['panel']) for t in targets}
            if overlap and any(k in previous['changes'] and previous['changes'][k] != v for k, v in changes.items()):
                raise ValueError('同じコマへの場面例外が矛盾しています。')
        result.append(dict(targets=targets, changes=changes, reason=reason))
    return result


def normalize_features(values):
    if not isinstance(values, dict) or not set(values) <= set(FIXED_FIELDS):
        raise ValueError('固定特徴・衣装の項目名が不正です。')
    return {key: text(value, FIXED_FIELDS[key], 2000, True) for key, value in values.items()}


def validate(data, project_id):
    if not isinstance(data, dict) or set(data) != set(empty(project_id)) or type(data['schema_version']) is not int or data['schema_version'] != 1 or data['format'] != 'manga-character-ai' or data['project_id'] != project_id:
        raise ValueError('キャラクターバイブルの形式が不正です。')
    if type(data['revision']) is not int or data['revision'] < 0:
        raise ValueError('版番号が不正です。')
    seen = set()
    for collection in ('bibles', 'observations', 'checks', 'prompts', 'events'):
        if not isinstance(data[collection], list):
            raise ValueError
        for row in data[collection]:
            validate_id(row['id'])
            if row['id'] in seen or datetime.fromisoformat(row['saved_at']).tzinfo is None:
                raise ValueError('履歴ID・日時が不正です。')
            seen.add(row['id'])
    for row in data['bibles']:
        validate_id(row['character_id']); validate_id(row['story_version_id'])
        if character_fields(row['profile']) != row['profile'] or normalize_story(row['source_story']).story != row['source_story']:
            raise ValueError('バイブルの設定・元脚本が不正です。')
        if not isinstance(row['locked_fields'], list) or len(set(row['locked_fields'])) != len(row['locked_fields']) or not set(row['locked_fields']) <= set(FIXED_FIELDS):
            raise ValueError('ロック項目が不正です。')
        if any(not row['profile'].get(k) for k in row['locked_fields']):
            raise ValueError('空欄の特徴はロックできません。')
        if not isinstance(row['aliases'], list) or len(row['aliases']) > 30:
            raise ValueError('別名は30件までです。')
        for name in row['aliases']: text(name, '確認済み別名', 100, True)
        text(row['reason'], '採用根拠', 2000, True)
        if normalize_exceptions(row['exceptions'], row['source_story']) != row['exceptions']:
            raise ValueError
        if not isinstance(row['references'], list): raise ValueError
        ref_ids = []
        for ref in row['references']:
            validate_id(ref['id']); ref_ids.append(ref['id'])
            if ref['path'] != f"images/references/{ref['id']}.png": raise ValueError('参照画像パスが不正です。')
            if ref['sha256'] is not None and (len(ref['sha256']) != 64 or any(c not in '0123456789abcdef' for c in ref['sha256'])): raise ValueError('参照画像ハッシュが不正です。')
        if len(set(ref_ids)) != len(ref_ids) or row['primary_reference_id'] is not None and row['primary_reference_id'] not in ref_ids:
            raise ValueError('基準画像IDが不正です。')
    for row in data['observations']:
        validate_id(row['character_id']); validate_id(row['story_version_id'])
        from manga.artwork_models import panel_key
        panel_key(row['page'], row['panel'])
        if normalize_features(row['fields']) != row['fields']: raise ValueError
        text(row['reason'], 'コマ設定の根拠', 2000, True)
    for row in data['checks']:
        validate_id(row['story_version_id'])
        if not isinstance(row['issues'], list) or not isinstance(row['source_token'], str): raise ValueError
    for row in data['prompts']:
        validate_id(row['story_version_id'])
        text(row['prompt'], '画像生成プロンプト', 200000, True)
        if row['kind'] not in ('character', 'panel'): raise ValueError
    return data
