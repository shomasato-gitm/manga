"""設定・ID・版・参照の静的検査。自然文や画像の意味を理解したとは扱わない。"""
import re

from manga.artwork_models import image_context_changed, panel_key
from manga.character_ai_extraction import matches
from manga.character_ai_models import FIXED_FIELDS
from manga.character_ai_service import bible_stale, effective_profile, latest_bibles
from manga.page_dialogue import split_dialogue


def directive_features(direction, characters, aliases):
    """『悠真：髪色=黒、服装=制服』等の明示ラベルだけを扱う。"""
    parsed, ambiguous = [], []
    for key, value in direction.items():
        if key not in ('composition', 'continuity', 'pose', 'expression', 'background', 'color_palette') or not isinstance(value, str):
            continue
        for line in value.splitlines():
            fields = {}
            for field, label in FIXED_FIELDS.items():
                match = re.search(re.escape(label) + r'[：:=]\s*([^\n、。；;]+)', line)
                if match: fields[field] = match[1].strip()
            if not fields: continue
            prefix = re.split(r'[：:]', line, maxsplit=1)[0].strip()
            options = matches(prefix, characters, aliases)
            ids = [r['character_id'] for r in options]
            if not ids and len(direction['character_ids']) == 1:
                ids = direction['character_ids']
            if len(ids) != 1:
                ambiguous.append(dict(field=key, text=line, reason='固定特徴の対象人物が曖昧です。人物名付きラベルで確認してください。'))
            else:
                parsed.append(dict(character_id=ids[0], fields=fields, reason=f'{key}：{line}', origin='direction'))
    return parsed, ambiguous


def inspect(snapshot):
    latest, art, data = snapshot['latest'], snapshot['artwork'], snapshot['data']
    bibles = latest_bibles(data)
    characters = {c['id']: c for c in art['characters'] if not c['deleted_at']}
    aliases = {k: b['aliases'] for k, b in bibles.items()}
    issues = []

    def issue(kind, message, page=None, panel=None, character_id=None, severity='要確認', **details):
        issues.append(dict(kind=kind, message=message, page=page, panel=panel, character_id=character_id, severity=severity, **details))

    for person_id, person in characters.items():
        bible = bibles.get(person_id)
        assets = snapshot['references'][person_id]
        if assets['error']:
            issue('unsafe_reference', assets['error'], character_id=person_id)
        if not person['primary_reference_id']:
            issue('reference_unregistered', f"{person['name']}の基準画像は未登録です。外見は未確認です。", character_id=person_id)
        for ref in assets['images']:
            if ref['state'] == 'missing':
                issue('reference_missing', f"{person['name']}の参照画像ファイルが欠損しています。", character_id=person_id, reference_id=ref['id'])
        if bible is None:
            issue('bible_unconfirmed', f"{person['name']}のバイブルが未確認です。", character_id=person_id)
        elif assets['error'] or bible_stale(bible, latest, person, assets['images']):
            issue('bible_stale', f"{person['name']}の脚本・人物設定・参照画像がバイブル作成時から変更されています。", character_id=person_id, bible_id=bible['id'])
        collisions = [m for m in matches(person['name'], list(characters.values()), aliases) if m['character_id'] != person_id]
        if collisions:
            issue('identity_ambiguous', f"{person['name']}には同名・別名・一部一致の人物候補があります。同一人物と断定できません。", character_id=person_id, candidates=collisions)

    if not characters:
        issue('no_characters', '正式登録された人物がありません。人物のない作品か、登録前かを人間が確認してください。')
    for row in snapshot['observations']:
        if row['story_version_id'] != latest['id']:
            issue('observation_stale', '過去の脚本版に対するコマ設定です。現在の脚本には自動適用しません。', row['page'], row['panel'], row['character_id'])

    page_values = {}
    for page in latest['story']['pages']:
        for panel in page['panels']:
            p, c = page['page'], panel['panel']
            entry = art['panels'].get(panel_key(p, c)) or {}
            direction = entry.get('direction')
            adopted = next((i for i in entry.get('candidates', []) if i['id'] == entry.get('selected_image_id')), None)
            if adopted and (adopted['story_version_id'] != latest['id'] or adopted['source_panel'] != panel or image_context_changed(adopted, entry, art)):
                issue('image_context_stale', '採用画像は以前の脚本・作画・人物設定に基づいています。画像を保持し、人間による再確認が必要です。', p, c, image_id=adopted['id'])
            if not direction:
                issue('direction_missing', '作画指示が未設定です。', p, c)
                continue
            if direction['story_version_id'] != latest['id'] or direction['source_panel'] != panel:
                issue('direction_stale', '作画指示の脚本版または元コマが古くなっています。', p, c)
            for identifier in direction['character_ids']:
                if identifier not in characters:
                    issue('unknown_character_id', '作画指示の人物IDが存在しないか削除済みです。', p, c, identifier)
            for utterance in split_dialogue(panel['dialogue']):
                candidates = matches(utterance.speaker, list(characters.values()), aliases) if utterance.speaker else []
                if utterance.needs_review or len(candidates) != 1:
                    issue('speaker_unconfirmed', 'セリフの話者を一意に確認できません。原文を人間が確認してください。', p, c)
                elif candidates[0]['character_id'] not in direction['character_ids']:
                    issue('speaker_direction_mismatch', f"話者「{utterance.speaker}」の人物IDが作画指示にありません。画面外の発言か確認してください。", p, c, candidates[0]['character_id'])
            features, ambiguous = directive_features(direction, list(characters.values()), aliases)
            for row in ambiguous:
                issue('feature_identity_ambiguous', row['reason'], p, c, evidence=row['text'])
            features += [dict(character_id=r['character_id'], fields=r['fields'], reason=r['reason'], origin='panel_settings') for r in snapshot['observations']
                         if r['story_version_id'] == latest['id'] and r['page'] == p and r['panel'] == c]
            for row in features:
                identifier = row['character_id']
                if identifier not in direction['character_ids']:
                    issue('name_id_mismatch', '記述された人物と登場人物IDが混線しています。', p, c, identifier, evidence=row['reason'])
                bible = bibles.get(identifier)
                if bible is None: continue
                expected, exceptions = effective_profile(bible, p, c)
                for key, observed in row['fields'].items():
                    if key in bible['locked_fields'] and observed != expected.get(key, ''):
                        issue('fixed_conflict', f"{FIXED_FIELDS[key]}が固定設定／承認済み例外と異なります。", p, c, identifier,
                              field=key, expected=expected.get(key, ''), observed=observed, evidence=row['reason'], bible_id=bible['id'])
                    value_key = (p, identifier, key)
                    previous = page_values.setdefault(value_key, [])
                    allowed = any(key in e['changes'] for e in exceptions)
                    if any(v['panel'] != c and v['value'] != observed and not v['approved_exception'] for v in previous) and not allowed:
                        issue('page_continuity', '同一ページの別コマで設定が変化しています。衣装・回想等の例外を確認してください。', p, c, identifier,
                              field=key, observed=observed, previous=previous.copy(), evidence=row['reason'])
                    previous.append(dict(panel=c, value=observed, approved_exception=allowed))
            prose = '\n'.join(v for v in direction.values() if isinstance(v, str))
            for identifier in direction['character_ids']:
                bible = bibles.get(identifier)
                if bible is None: continue
                expected, _ = effective_profile(bible, p, c)
                missing = [FIXED_FIELDS[k] for k in bible['locked_fields'] if expected.get(k) and expected[k] not in prose]
                if missing:
                    issue('important_features_unspecified', '作画指示の本文に固定特徴が明記されていません。統一プロンプトではバイブルを補足します。', p, c, identifier,
                          fields=missing, severity='補足情報')
    return issues
