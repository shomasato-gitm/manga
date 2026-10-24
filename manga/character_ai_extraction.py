"""明確な話者・登録名・設定ラベルを抽出する無料モック。人物同定は行わない。"""
import re

from manga.artwork_models import CHARACTER_ALL_FIELDS
from manga.assistant_changes import name_key
from manga.artwork_models import text
from manga.page_dialogue import split_dialogue

AMBIGUOUS = {'彼', '彼女', '私', '俺', '僕', 'あなた', '君', '先生', '生徒', '男', '女', '誰か', '不明', '???', 'ナレーター', 'ナレーション'}
HONORIFIC = re.compile(r'(さん|さま|様|ちゃん|くん|君|先生|先輩|後輩|殿)$')


def matching_key(name):
    value = name_key(name)
    match = HONORIFIC.search(value)
    return value[:match.start()] if match and match.start() else value


def matches(name, characters, aliases=None):
    """完全一致も候補にすぎない。名字／名前だけの一致は弱い候補として返す。"""
    result = []
    for person in characters:
        if person['deleted_at']:
            continue
        names = [person['name'], *(aliases or {}).get(person['id'], [])]
        reasons = []
        for candidate in names:
            a, b = matching_key(name), matching_key(candidate)
            if a == b:
                reasons.append('表記・空白・敬称を正規化すると一致（本人の確認が必要）')
            elif min(len(a), len(b)) >= 2 and (a.startswith(b) or a.endswith(b) or b.startswith(a) or b.endswith(a)):
                reasons.append('名字／名前の一部が一致する候補（別人の可能性）')
        if reasons:
            result.append(dict(character_id=person['id'], name=person['name'], reasons=sorted(set(reasons))))
    return result


def extract(story, artwork, *, additional_names=(), aliases=None):
    if not isinstance(additional_names, (list, tuple)) or len(additional_names) > 50:
        raise ValueError('追加する人物名は50件までです。')
    additional = [text(n, '追加する人物名', 100, True) for n in additional_names]
    candidates, unknown, narration = {}, [], []
    panels = [(p['page'], c) for p in story['pages'] for c in p['panels']]
    if sum(len(c[k]) for _, c in panels for k in ('description', 'dialogue', 'narration')) > 500000:
        raise ValueError('抽出対象の本文が長すぎます。50万文字以内で確認してください。')

    def add(name, page, panel, field, quote):
        name = text(name, '候補名', 100, True)
        key = name_key(name)
        entry = candidates.setdefault(key, dict(name=name, sources=[], explicit_fields={}))
        source = dict(page=page, panel=panel, field=field, quote=quote)
        if source not in entry['sources']:
            entry['sources'].append(source)
        if len(candidates) > 50:
            raise ValueError('人物候補が50件を超えました。脚本の話者を整理してください。')

    known = [c['name'] for c in artwork['characters'] if not c['deleted_at']]
    known += [alias for c in artwork['characters'] if not c['deleted_at'] for alias in (aliases or {}).get(c['id'], [])]
    for page, panel in panels:
        for utterance in split_dialogue(panel['dialogue']):
            if utterance.needs_review or not utterance.speaker or matching_key(utterance.speaker) in AMBIGUOUS:
                unknown.append(dict(page=page, panel=panel['panel'], field='dialogue', quote=utterance.raw, reason='話者が曖昧です。原文を保持し、人間による確認が必要です。'))
            else:
                add(utterance.speaker, page, panel['panel'], 'dialogue', utterance.raw)
        if panel['narration']:
            narration.append(dict(page=page, panel=panel['panel'], field='narration', quote=panel['narration']))
        for field in ('description', 'narration'):
            value = panel[field]
            for name in known + additional:
                if name in value:
                    add(name, page, panel['panel'], field, value)
            for marked in re.findall(r'人物[：:]\s*([^\n、。：「」]{1,40})', value):
                if matching_key(marked) not in AMBIGUOUS:
                    add(marked, page, panel['panel'], field, value)
        for name in additional:
            if name in panel['dialogue']:
                add(name, page, panel['panel'], 'dialogue', panel['dialogue'])
    for name in additional:
        if name_key(name) not in candidates:
            raise ValueError(f'追加名「{name}」の記述根拠が脚本にありません。脚本か名前を確認してください。')

    result = []
    for index, candidate in enumerate(candidates.values(), 1):
        for source in candidate['sources']:
            # 一つの文に複数人物が現れる場合、属性を一人へ割り当てない。
            mentioned = [c for c in candidates.values() if c['name'] in source['quote']]
            if len(mentioned) != 1:
                continue
            for key, label in CHARACTER_ALL_FIELDS.items():
                if key == 'name':
                    continue
                found = re.search(re.escape(label) + r'[：:=]\s*([^\n、。；;]+)', source['quote'])
                if found:
                    candidate['explicit_fields'].setdefault(key, []).append(dict(value=found[1].strip(), source=source))
        candidate.update(id=f'character-{index}', matches=matches(candidate['name'], artwork['characters'], aliases))
        result.append(candidate)
    return dict(candidates=result, unknown_speakers=unknown, narrator=dict(name='ナレーター', sources=narration),
                limitations='モックは明確な話者・登録名・追加指定名・設定ラベルだけを抽出します。描写だけの未登録人物は名前を追加指定し、不明事項を確認してください。')


def mock_items(extraction):
    items = []
    for index, candidate in enumerate(extraction['candidates']):
        fields = {key: '' for key in CHARACTER_ALL_FIELDS}
        fields['name'] = candidate['name']
        source = candidate['sources'][0]
        provenance = dict(name=dict(status='ストーリー明記', evidence=f"{source['page']}ページ{source['panel']}コマ / {source['field']}：{source['quote']}"[:2000]))
        questions = ['年齢・性別等の未指定情報は空欄です。既存人物との同定と設定を確認してください。']
        for key, values in candidate['explicit_fields'].items():
            if len({v['value'] for v in values}) == 1:
                fields[key] = values[0]['value']
                src = values[0]['source']
                provenance[key] = dict(status='ストーリー明記', evidence=f"{src['page']}ページ{src['panel']}コマ：{src['quote']}"[:2000])
            else:
                questions.append(f'{CHARACTER_ALL_FIELDS[key]}の記述が複数あり要確認です。')
        if not fields['visual_identity']:
            fields['visual_identity'] = ('丸い留め具', '角形の小物', '縞模様のアクセント')[index % 3]
            provenance['visual_identity'] = dict(status='AI提案', evidence='無料モックが新たに提案した識別特徴。脚本の事実ではありません。')
        items.append(dict(id=candidate['id'], fields=fields, existing_id=None, provenance=provenance, questions=questions))
    return items
