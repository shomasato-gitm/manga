"""既存AssistantStoreの人物提案・差分変換・採用ジャーナルを拡張する。APIなし。"""
import copy

from manga.artwork_models import CHARACTER_ALL_FIELDS, character_fields
from manga.assistant_changes import apply_items
from manga.assistant_models import normalize_item, normalize_payload
from manga.assistant_prompts import production_prompt
from manga.assistant_storage import AssistantStore, record
from manga.character_ai_extraction import extract, matches, mock_items
from manga.character_ai_storage import CharacterAIStore
from manga.models import now_iso
from manga.storage import StorageError
from manga.voice_models import digest


class CharacterProposals(AssistantStore):
    def __init__(self, directory, project_id):
        super().__init__(directory, project_id)
        self.bibles = CharacterAIStore(directory, project_id)

    def locked(self):
        return self.bibles.locked()

    def finalized(self, version_id):
        version = self.latest(version_id)
        if not any(f['story_version_id'] == version_id and f['source_story'] == version['story']
                   for f in self.editorial.load()['finalizations']):
            raise ValueError('最新の正式脚本を先に「比較・脚本確定」で確認・確定してください。候補版は対象にしません。')
        return version

    def create_mock(self, project, version_id, *, expected_revision, additional_names=(), new_request=False):
        if project.id != self.project_id or type(new_request) is not bool:
            raise ValueError('作品・依頼設定を確認してください。')
        with self.locked():
            data = self.checked(expected_revision)
            latest = self.finalized(version_id)
            artwork, editorial = self.artwork.load(), self.editorial.load()
            bibles = self.bibles.load()
            aliases = {b['character_id']: b['aliases'] for b in bibles['bibles']}
            extraction = extract(latest['story'], artwork, additional_names=additional_names, aliases=aliases)
            fingerprint = digest(dict(version=latest, characters=artwork['characters'], additional=list(additional_names), aliases=aliases))
            if not new_request:
                old = next((r for r in reversed(data['requests']) if r.get('character_ai', {}).get('fingerprint') == fingerprint), None)
                if old:
                    proposal = next((p for p in reversed(data['proposals']) if p['request_id'] == old['id']), None)
                    return dict(request=old, proposal=proposal)
            request = record(project_id=self.project_id, kind='characters', story_version_id=version_id, targets=[],
                             theme='Phase 2-C：同定は人間が確認。不明属性を事実として補完しない。', evaluation_id=None,
                             context=copy.deepcopy(dict(story=latest['story'], artwork=artwork, references=editorial['references'])))
            request['prompt'] = production_prompt(project, request)
            request['character_ai'] = dict(provider='mock', fingerprint=fingerprint, extraction=extraction,
                                           paid_api_calls=0, api_cost_jpy=0)
            data['requests'].append(request)
            items = mock_items(extraction)
            proposal = None
            if items:
                payload = normalize_payload(dict(schema_version=1, request_id=request['id'], project_id=self.project_id,
                    story_version_id=version_id, kind='characters', items=items), request)
                proposal = record(request_id=request['id'], payload=payload, original_payload=copy.deepcopy(payload), edits=[],
                                  decisions={item['id']: '確認待ち' for item in payload['items']})
                data['proposals'].append(proposal)
            # 依頼とモック応答を同じ既存JSONへ一度に保存。中間状態を作らない。
            self.save(data)
            return dict(request=request, proposal=proposal)

    def adopt_fields(self, proposal_id, item_id, values, fields, existing_id, *, expected_revision,
                     expected_artwork_revision, expected_editorial_revision, expected_character_revision,
                     identity_confirmed=False, distinct_confirmed=False):
        if identity_confirmed is not True:
            raise ValueError('人物の同定と採用する項目を明示的に確認してください。')
        if not isinstance(fields, list) or not fields or len(set(fields)) != len(fields) or not set(fields) <= set(CHARACTER_ALL_FIELDS):
            raise ValueError('採用する項目を重複なく選んでください。')
        with self.locked():
            before = self.checked(expected_revision)
            data = copy.deepcopy(before)
            bible_data = self.bibles.checked(expected_character_revision)
            proposal = self.proposal(data, proposal_id)
            request = self.request(data, proposal['request_id'])
            if request['kind'] != 'characters': raise ValueError('人物提案を選択してください。')
            self.finalized(request['story_version_id'])
            if proposal['decisions'].get(item_id) != '確認待ち': raise ValueError('確認待ちの提案だけを採用できます。二重登録しません。')
            item = next(i for i in proposal['payload']['items'] if i['id'] == item_id)
            artwork, editorial = self.artwork.load(), self.editorial.load()
            if type(expected_artwork_revision) is not int or artwork['revision'] != expected_artwork_revision or type(expected_editorial_revision) is not int or editorial['revision'] != expected_editorial_revision:
                raise StorageError('人物・作画・確定状態が更新されました。差分を再確認してください。')
            current = None
            if existing_id is not None:
                current = self.artwork._character(artwork, existing_id)
                if existing_id not in {c['id'] for c in request['context']['artwork']['characters']}:
                    raise ValueError('依頼にない人物IDです。新しい依頼を作成してください。')
            elif 'name' not in fields:
                raise ValueError('新規人物には名前の採用が必要です。')
            merged = {key: (current.get(key, '') if current else '') for key in CHARACTER_ALL_FIELDS}
            for key in fields:
                if key not in values: raise ValueError('採用項目の値が不足しています。')
                merged[key] = values[key]
            merged = character_fields(merged)
            aliases = {b['character_id']: b['aliases'] for b in bible_data['bibles']}
            if current is None and matches(merged['name'], artwork['characters'], aliases) and distinct_confirmed is not True:
                raise ValueError('既存人物との重複候補です。同じ人物を選択するか、別人としての登録を確認してください。')
            bible = next((b for b in reversed(bible_data['bibles']) if b['character_id'] == existing_id), None)
            if bible and any(merged.get(k, '') != current.get(k, '') for k in fields if k in bible['locked_fields']):
                raise ValueError('固定特徴がロックされています。バイブルで明示的にロックを解除するか、場面例外を登録してください。')
            reviewed = copy.deepcopy(item)
            reviewed.update(existing_id=existing_id, fields=merged)
            reviewed['provenance'] = {}
            for key, value in merged.items():
                if not value: continue
                if key in fields and value == item['fields'].get(key) and key in item['provenance']:
                    reviewed['provenance'][key] = copy.deepcopy(item['provenance'][key])
                else:
                    reviewed['provenance'][key] = dict(status='ユーザー編集', evidence='部分採用の確認画面で編集' if key in fields else '選択外の既存設定を維持')
            reviewed = normalize_item('characters', reviewed, request)
            # 同名別人が既に存在しても、明示選択したUUIDの名前を維持する更新は可能。
            duplicate_allowed = distinct_confirmed is True or current is not None and merged['name'] == current['name']
            target, changes = apply_items('characters', [reviewed], request, artwork, editorial, allow_duplicates=duplicate_allowed)
            proposal['edits'].append(dict(item_id=item_id, saved_at=now_iso(), before=copy.deepcopy(item), after=copy.deepcopy(reviewed)))
            item.clear(); item.update(reviewed)
            proposal['decisions'][item_id] = '採用済み'
            data['adoptions'].append(record(proposal_id=proposal_id, target='artwork', changes=changes,
                character_ai=dict(selected_fields=fields, identity_confirmed=True, distinct_confirmed=distinct_confirmed is True)))
            self.commit_adoption(before, data, 'artwork', artwork, target)
            return changes[0]

    def resume(self):
        from manga.character_ai_recovery import validate_recovery
        with self.locked():
            pending = self.pending()
            if pending:
                validate_recovery(pending)
                self._finish(pending)
        return True
