"""確認済みバイブル・場面例外・静的検査・プロンプトの独立した履歴。"""
import copy

from manga.artwork_models import character_fields, panel_key, text
from manga.character_ai_assets import references
from manga.character_ai_models import FIXED_FIELDS, normalize_exceptions, normalize_features
from manga.character_ai_proposals import CharacterProposals
from manga.character_ai_storage import record
from manga.models import validate_id
from manga.storage import StorageError
from manga.voice_models import digest


def latest_bibles(data):
    return {b['character_id']: b for b in data['bibles']}


def bible_stale(bible, latest, character, refs):
    if character is None or character['deleted_at'] or bible['story_version_id'] != latest['id'] or bible['profile'] != character_fields(character) or bible['primary_reference_id'] != character['primary_reference_id']:
        return True
    def identity(ref):
        return {k:ref[k] for k in ('id', 'path', 'view', 'note', 'state', 'size')}
    if [identity(r) for r in bible['references']] != [identity(r) for r in refs]: return True
    for before, after in zip(bible['references'], refs):
        if after['sha256'] is not None:
            if before['sha256'] != after['sha256']: return True
        elif before['signature'] != after['signature']: return True
    return False


def effective_profile(bible, page=None, panel=None):
    profile = copy.deepcopy(bible['profile'])
    applied = []
    for exception in bible['exceptions']:
        if dict(page=page, panel=panel) in exception['targets']:
            profile.update(exception['changes']); applied.append(exception)
    return profile, applied


def source_snapshot(latest, artwork, data, root, *, hashed=False):
    """既存Storeの保存値から読み取り専用の素材スナップショットを作る。"""
    refs = {}
    for person in artwork['characters']:
        if person['deleted_at']: continue
        try: refs[person['id']] = dict(images=references(root, person, hashed=hashed), error=None)
        except (ValueError, OSError, StorageError) as error: refs[person['id']] = dict(images=[], error=str(error))
    observations = {}
    for observation in data['observations']:
        key = (observation['story_version_id'], observation['page'], observation['panel'], observation['character_id'])
        observations[key] = observation
    # shaは別途バイブル比較で検査する。軽量な更新検知にも同じtokenを使う。
    ref_state = {k:dict(error=v['error'], images=[{a:b for a,b in r.items() if a != 'sha256'} for r in v['images']]) for k,v in refs.items()}
    relevant = dict(story=latest, artwork=artwork, references=ref_state, bibles=list(latest_bibles(data).values()), observations=list(observations.values()))
    return dict(latest=latest, artwork=artwork, data=data, references=refs, observations=list(observations.values()), token=digest(relevant))

class CharacterAIService:
    def __init__(self, directory, project_id):
        self.proposals = CharacterProposals(directory, project_id)
        self.store = self.proposals.bibles
        self.artwork = self.proposals.artwork
        self.stories = self.proposals.stories
        self.project_id = self.store.project_id

    def snapshot(self, *, hashed=False):
        latest = self.proposals.latest()
        artwork = self.artwork.load()
        data = self.store.load()
        return source_snapshot(latest, artwork, data, self.store.root, hashed=hashed)

    def save_bible(self, character_id, *, version_id, expected_revision, expected_artwork_revision,
                   locked_fields, aliases, exceptions, reason, confirmed=False):
        if confirmed is not True: raise ValueError('固定設定・参照画像・例外と採用根拠を確認してください。')
        validate_id(character_id)
        if not isinstance(locked_fields, list) or len(set(locked_fields)) != len(locked_fields) or not set(locked_fields) <= set(FIXED_FIELDS):
            raise ValueError('ロックする固定項目を確認してください。')
        if not isinstance(aliases, list) or len(aliases) > 30: raise ValueError('別名は30件までです。')
        names = [text(n, '確認済み別名', 100, True) for n in aliases]
        from manga.assistant_changes import name_key
        if len({name_key(n) for n in names}) != len(names): raise ValueError('別名が重複しています。')
        with self.store.locked():
            data = self.store.checked(expected_revision)
            latest = self.proposals.finalized(version_id)
            art = self.artwork.load()
            if type(expected_artwork_revision) is not int or art['revision'] != expected_artwork_revision:
                raise StorageError('人物・参照画像が更新されました。再確認してください。')
            character = self.artwork._character(art, character_id)
            profile = character_fields(character)
            if any(not profile.get(k) for k in locked_fields): raise ValueError('空欄の特徴はロックできません。')
            refs = references(self.store.root, character, hashed=True)
            changes = normalize_exceptions(exceptions, latest['story'])
            source_adoptions = [a['id'] for a in self.proposals.load()['adoptions'] if any(c['result_id'] == character_id for c in a['changes'])]
            bible = record(character_id=character_id, story_version_id=version_id, source_story=copy.deepcopy(latest['story']),
                profile=profile, locked_fields=locked_fields, variable_elements=['表情', '姿勢', '照明'], aliases=names,
                exceptions=changes, references=refs, primary_reference_id=character['primary_reference_id'],
                source_adoption_ids=source_adoptions, reason=text(reason, '採用根拠', 2000, True))
            data['bibles'].append(bible)
            self.store.save(data, 'bible_confirmed', bible_id=bible['id'])
            return bible

    def save_observation(self, page, panel, character_id, fields, reason, *, version_id, expected_revision, expected_artwork_revision):
        panel_key(page, panel); validate_id(character_id)
        values = normalize_features(fields)
        with self.store.locked():
            data = self.store.checked(expected_revision)
            latest = self.proposals.finalized(version_id)
            from manga.assistant_models import validate_targets
            validate_targets([dict(page=page, panel=panel)], latest['story'])
            art = self.artwork.load()
            if type(expected_artwork_revision) is not int or art['revision'] != expected_artwork_revision:
                raise StorageError('作画指示・人物が更新されました。再確認してください。')
            self.artwork._character(art, character_id)
            direction = (art['panels'].get(panel_key(page, panel)) or {}).get('direction')
            if not direction or direction['story_version_id'] != version_id or character_id not in direction['character_ids']:
                raise ValueError('このコマの最新の作画指示へ登場人物を登録してください。')
            row = record(page=page, panel=panel, character_id=character_id, fields=values, reason=text(reason, 'コマ設定の根拠', 2000, True), story_version_id=version_id)
            data['observations'].append(row)
            self.store.save(data, 'panel_settings_recorded', observation_id=row['id'])
            return row

    def run_checks(self, *, expected_revision):
        from manga.character_ai_checks import inspect
        with self.store.locked():
            data = self.store.checked(expected_revision)
            snapshot = self.snapshot(hashed=True)
            result = record(story_version_id=snapshot['latest']['id'], source_token=snapshot['token'], issues=inspect(snapshot),
                            pages=len(snapshot['latest']['story']['pages']), panels=sum(len(p['panels']) for p in snapshot['latest']['story']['pages']))
            data['checks'].append(result)
            self.store.save(data, 'static_check', check_id=result['id'])
            return result

    def prompt(self, kind, **values):
        from manga.character_ai_prompts import build_prompt
        return build_prompt(self, kind, **values)

    def save_prompt(self, package, *, expected_revision):
        with self.store.locked():
            data = self.store.checked(expected_revision)
            if self.snapshot()['token'] != package['source_token']:
                raise StorageError('設定が変更されました。プロンプトを作成し直してください。')
            row = record(**copy.deepcopy(package))
            data['prompts'].append(row)
            self.store.save(data, 'prompt_saved', prompt_id=row['id'])
            return row
