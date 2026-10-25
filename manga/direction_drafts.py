"""正式脚本の明記箇所だけを下書きへ移す。正本の保存はArtworkStoreに委譲する。"""
import re

from manga.artwork_models import DIRECTION_FIELDS, direction_fields, panel_key
from manga.character_ai_extraction import extract
from manga.character_ai_service import latest_bibles
from manga.character_ai_storage import CharacterAIStore
from manga.editorial_storage import EditorialStore
from manga.storage import StorageError
from manga.voice_models import digest


def _aliases(store):
    data = CharacterAIStore(store.root.parent, store.project_id).load()
    return {identifier: bible['aliases'] for identifier, bible in latest_bibles(data).items()}


def script_hints(latest, artwork, aliases=None):
    """人物照合は候補。自然文から場所・感情等を推測して確定しない。"""
    extraction = extract(latest['story'], artwork, aliases=aliases)
    rows = []
    labels = dict(DIRECTION_FIELDS, aspect_ratio='画像アスペクト比')
    for page in latest['story']['pages']:
        for panel in page['panels']:
            p, c = page['page'], panel['panel']
            ids, evidence, issues = [], [], []
            for candidate in extraction['candidates']:
                sources = [s for s in candidate['sources'] if (s['page'], s['panel']) == (p, c)]
                if not sources:
                    continue
                evidence.append(candidate['name']+'：'+sources[0]['quote'])
                if len(candidate['matches']) != 1:
                    issues.append('人物「'+candidate['name']+'」の登録先を一意に選べません。人物登録・同定後に個別入力してください。')
                else:
                    identifier = candidate['matches'][0]['character_id']
                    if identifier not in ids:
                        ids.append(identifier)
            if any((s['page'], s['panel']) == (p, c) for s in extraction['unknown_speakers']):
                issues.append('セリフの話者が不明です。脚本と人物を確認し、個別入力してください。')
            if not ids:
                issues.append('登場人物を特定できません。人物のいないコマかも含め、個別入力で確認してください。')
            fields = direction_fields(dict(character_ids=ids))
            quoted = panel['description']+'\n'+panel['narration']
            hints = {}
            for field, label in labels.items():
                values = re.findall(r'(?:^|[\n、。；;])\s*'+re.escape(label)+r'[：:=]\s*([^\n、。；;]+)', quoted)
                values = list(dict.fromkeys(v.strip() for v in values if v.strip()))
                if len(values) == 1:
                    hints[field] = values[0]
                    if field in fields:
                        fields[field] = values[0]
                elif len(values) > 1:
                    issues.append(label+'の明記が複数あります。個別入力で確認してください。')
            rows.append(dict(page=p, panel=c, key=panel_key(p, c), source_panel=panel,
                             fields=fields, hints=hints, evidence=evidence, issues=issues))
    return rows


def _finalized(store, latest):
    records = EditorialStore(store.root.parent, store.project_id).load()['finalizations']
    if not any(r['story_version_id'] == latest['id'] and r['source_story'] == latest['story'] for r in records):
        raise ValueError('最新の脚本が未確定です。下書きの元を固定するため、「企画・脚本」の「比較・脚本確定」で確認・確定してください。')


def prepare_drafts(store):
    history = store.story_store.load_history()
    if not history:
        raise ValueError('正式脚本が未登録です。「企画・脚本」で脚本を取り込んでください。')
    latest, artwork = history[-1], store.load()
    _finalized(store, latest)
    aliases = _aliases(store)
    rows = script_hints(latest, artwork, aliases)
    for row in rows:
        row['existing'] = bool((artwork['panels'].get(row['key']) or {}).get('direction'))
    return dict(project_id=store.project_id, story_version_id=latest['id'], revision=artwork['revision'],
                aliases_token=digest(aliases), items=rows)


def save_drafts(store, batch, selected, *, confirmed=False):
    """一件ずつ既存の競合検知付き保存。部分失敗は保存済み対象と未保存理由を返す。"""
    if confirmed is not True or not selected or len(set(selected)) != len(selected):
        raise ValueError('保存するコマを選び、人物候補・空欄の任意項目を明示確認してください。')
    artwork = store.load()
    history = store.story_store.load_history()
    if batch['project_id'] != store.project_id or not history or history[-1]['id'] != batch['story_version_id']:
        raise StorageError('脚本版または作品が変わりました。古い下書きは保存せず、最新脚本から準備し直してください。')
    _finalized(store, history[-1])
    if artwork['revision'] != batch['revision'] or digest(_aliases(store)) != batch['aliases_token']:
        raise StorageError('人物・作画設定が更新されました。候補を準備し直してから比較・保存してください。')
    by_key = {r['key']: r for r in batch['items']}
    # 全対象を先に検証し、入力不足のバッチでは一件も保存しない。
    for key in selected:
        row = by_key.get(key)
        if not row or row['existing'] or (artwork['panels'].get(key) or {}).get('direction'):
            raise ValueError('既存の作画指示は一括保存で上書きできません。変更する場合は対象コマを開いて確認・保存してください。')
        if row['issues']:
            raise ValueError(key+'：'+' / '.join(row['issues']))
        fields = direction_fields(row['fields'])
        if panel_key(row['page'], row['panel']) != key or row['source_panel'] != history[-1]['story']['pages'][row['page']-1]['panels'][row['panel']-1]:
            raise ValueError('下書きの対象コマが現在の脚本と一致しません。準備し直してください。')
        active = {c['id'] for c in artwork['characters'] if not c['deleted_at']}
        if not set(fields['character_ids']) <= active:
            raise ValueError('候補の人物UUIDが現在の登録人物と一致しません。人物を確認して準備し直してください。')
    saved, errors = [], {}
    revision = batch['revision']
    for index, key in enumerate(selected):
        row = by_key[key]
        try:
            store.save_direction(row['page'], row['panel'], row['fields'], story_version_id=batch['story_version_id'], expected_revision=revision)
            saved.append(key)
            revision += 1
        except (ValueError, OSError, StorageError) as error:
            errors = {k: str(error) if k == key else '直前の保存が停止したため未実行です。' for k in selected[index:]}
            break
    return dict(saved=saved, errors=errors)
