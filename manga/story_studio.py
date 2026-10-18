"""制作依頼と明示保存を調整する。AI通信なし、既存保存処理を隔離コピーで再利用。"""
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from manga.artwork_models import CHARACTER_FIELDS
from manga.artwork_storage import ArtworkStore
from manga.editorial_models import CRITERIA, MODES, normalize_targets, text
from manga.editorial_storage import EditorialStore
from manga.models import now_iso
from manga.storage import ProjectStore, StorageError
from manga.story_storage import StoryStore
from manga.story_concept_models import normalize_response
from manga.story_concept_storage import ConceptStore, validate_concepts
from manga.story_studio_prompts import complete_prompt
from manga.story_studio_status import (selected_concept, lineage, references_for_version,
                                       exact_evaluation, cycle_limit, regression_notes)
from manga.story_techniques import select_techniques, stale_references
from manga.sync_manifest import json_bytes, MAX_JSON_BYTES, SyncConflict
from manga.sync_storage import SyncStore


def record(**values):
    return dict(id=str(uuid4()), saved_at=now_iso(), **values)


def same_settings(before, after):
    return {k: v for k, v in before.items() if k not in ('created_at', 'updated_at')} == {
        k: v for k, v in after.items() if k not in ('created_at', 'updated_at')}


class StudioService:
    def __init__(self, directory, project_id):
        self.directory = Path(directory)
        self.concepts = ConceptStore(directory, project_id)
        self.project_id = self.concepts.project_id
        self.transactions = SyncStore(directory, project_id)
        self.stories = StoryStore(directory, project_id)
        self.editorial = EditorialStore(directory, project_id)

    @contextmanager
    def locked(self, expected_revision):
        with self.transactions.locked(production=True):
            self.transactions.checked(self.transactions.load()['revision'])
            data = self.concepts.load()
            if type(expected_revision) is not int or data['revision'] != expected_revision:
                raise SyncConflict('制作履歴が更新されました。画面を更新してから操作してください。')
            yield data

    def context(self):
        project = ProjectStore(self.directory).load(self.project_id).to_dict()
        artwork = ArtworkStore(self.directory, self.project_id).load()
        characters = [dict(id=c['id'], **{key: c[key] for key in CHARACTER_FIELDS})
                      for c in artwork['characters'] if not c['deleted_at']]
        history = self.stories.load_history()
        counts = ([len(p['panels']) for p in history[-1]['story']['pages']]
                  if history and len(history[-1]['story']['pages']) == project['page_count']
                  else [3]*project['page_count'])
        return dict(project=project, characters=characters, panel_counts=counts), history

    def save(self, data, updates=None):
        data['revision'] += 1
        validate_concepts(data, self.project_id)
        content = json_bytes(data)
        if len(content) > MAX_JSON_BYTES:
            raise ValueError('制作履歴が8MiBを超えました。削除せず保存を停止しました。バックアップと別プロジェクトへの分割を検討してください。')
        after = dict(updates or {})
        after['story_concepts.json'] = content
        before = {name: self.transactions.target(name).read_bytes() if self.transactions.target(name).exists() else None for name in after}
        self.transactions.commit(before, after)

    def create_request(self, kind, *, expected_revision, choices=None, options=None, evaluation_id=None):
        from manga.story_concept_models import KINDS
        if kind not in KINDS:
            raise ValueError('制作依頼の種類が不正です。')
        with self.locked(expected_revision) as data:
            context, history = self.context()
            editorial = self.editorial.load()
            latest = history[-1] if history else None
            selection = None
            if kind == 'concepts':
                references, warnings = select_techniques(editorial, choices or {})
            elif kind == 'detail':
                selection, concept, source = selected_concept(data)
                if not selection:
                    raise ValueError('先に概要3案を比較し、1案を明示採用してください。')
                references = copy.deepcopy(source['context']['references'])
                warnings = stale_references(references, editorial)
                if warnings:
                    raise ValueError('参考分析が古くなっています。概要案を現行分析で作り直し、採用し直してください。')
                context['concept'] = copy.deepcopy(concept)
            else:
                if latest is None:
                    raise ValueError('先に詳細ストーリーを保存してください。')
                context['source_story'] = copy.deepcopy(latest['story'])
                references = copy.deepcopy(references_for_version(data, history, latest['id']))
                warnings = stale_references(references, editorial)
                origin = lineage(data, history, latest['id'])['origin']
                if origin:
                    detail_request = next(r for r in data['requests'] if r['id'] == origin['request_id'])
                    context['origin_concept'] = copy.deepcopy(detail_request['context']['concept'])
                    context['origin_selection_id'] = origin['selection_id']
                if kind=='evaluation' and latest.get('revision_context'):
                    previous=next((v for v in history if v['id']==latest['revision_context']['base_version_id']),None)
                    if previous:
                        from manga.story_comparison import compare_stories
                        context['previous_version_id']=previous['id']
                        context['previous_story']=copy.deepcopy(previous['story'])
                        context['actual_changes']=compare_stories(previous['story'],latest['story'])
                        old_evaluation=exact_evaluation(editorial,previous)
                        context['previous_evaluation']=copy.deepcopy(old_evaluation['payload']) if old_evaluation else None
            context['references'] = references
            request = record(project_id=self.project_id, kind=kind, context=context, warnings=warnings,
                             base_version_id=latest['id'] if latest else None,
                             selection_id=selection['id'] if selection else None,
                             concept_ids=[str(uuid4()) for _ in range(3)] if kind == 'concepts' else [],
                             evaluation_id=None, options={})
            if kind == 'improvement':
                evaluation = exact_evaluation(editorial, latest)
                if evaluation is None or (evaluation_id is not None and evaluation_id != evaluation['id']):
                    raise ValueError('最新の脚本版に対応する最新評価を使用してください。')
                chain = lineage(data, history, latest['id'])
                if chain['cycles'] >= cycle_limit(data, chain['root_version_id']):
                    raise ValueError('改善回数の目安に達しました。追加改善を明示承認するか最終確認へ進んでください。')
                request['evaluation_id'] = evaluation['id']
                context['evaluation'] = copy.deepcopy(evaluation['payload'])
                request['options'] = self.improvement_options(evaluation, latest['story'], options)
            request['prompt'] = complete_prompt(request)
            data['requests'].append(request)
            data['events'].append(record(kind='request', request_id=request['id']))
            self.save(data)
            return copy.deepcopy(request)

    @staticmethod
    def improvement_options(evaluation, story, supplied=None):
        rows = evaluation['payload']['criteria']
        criteria = [key for key, value in rows.items() if value['score'] < 10 or value['problems'] not in ('なし', '問題なし')]
        criteria = criteria or list(CRITERIA)
        targets = [target for key in criteria for target in rows[key]['targets']]
        reason = '\n'.join(f"{CRITERIA[key]}：{rows[key]['problems']}／{rows[key]['reason']}／{rows[key]['suggestion']}" for key in criteria)[:4000]
        values = dict(mode=MODES[0], criteria=criteria, targets=normalize_targets(targets, story), reason=reason)
        values.update(supplied or {})
        if values['mode'] not in MODES or not isinstance(values['criteria'], list) or not values['criteria'] or not set(values['criteria']) <= set(CRITERIA):
            raise ValueError('改善方式・評価項目が不正です。')
        values['targets'] = normalize_targets(values['targets'], story, required=values['mode'] == MODES[1])
        values['reason'] = text(values['reason'], '改善理由', required=True)
        return values

    def select(self, batch_id, concept_id, *, expected_revision):
        with self.locked(expected_revision) as data:
            batch = next((b for b in data['batches'] if b['id'] == batch_id), None)
            if batch is None or concept_id not in {c['id'] for c in batch['payload']['concepts']}:
                raise ValueError('概要候補が見つかりません。')
            source = next(r for r in data['requests'] if r['id'] == batch['request_id'])
            context, _ = self.context()
            if not same_settings(source['context']['project'], context['project']) or source['context']['characters'] != context['characters'] or stale_references(source['context']['references'], self.editorial.load()):
                raise ValueError('概要生成後に設定・参考分析が変わりました。現在の設定で概要を作成し直してください。')
            selection = record(batch_id=batch_id, concept_id=concept_id)
            data['selections'].append(selection)
            data['selected_id'] = selection['id']
            self.save(data)
            return selection

    def validate_response(self, raw, data=None):
        from manga.story import load_json_payload
        from manga.editorial_models import obj
        payload = obj(load_json_payload(raw) if isinstance(raw, str) else raw, '制作スタジオの回答')
        data = data if data is not None else self.concepts.load()
        request = next((r for r in data['requests'] if r['id'] == payload.get('request_id')), None)
        if request is None:
            raise ValueError('対応する制作依頼がありません。先にこのプロジェクトで依頼を作成してください。')
        context, history = self.context()
        latest = history[-1] if history else None
        if request['base_version_id'] != (latest['id'] if latest else None):
            raise ValueError('依頼後にストーリー版が変わりました。最新の版から依頼を作り直してください。')
        if not same_settings(request['context']['project'], context['project']) or request['context']['characters'] != context['characters']:
            raise ValueError('依頼後に作品設定・人物設定が変わりました。依頼を作り直してください。')
        if request['kind'] in ('concepts', 'detail') and stale_references(request['context']['references'], self.editorial.load()):
            raise ValueError('依頼後に参考作品・分析が変わりました。現行分析で依頼を作り直してください。')
        if request['kind'] == 'detail' and data['selected_id'] != request['selection_id']:
            raise ValueError('採用する概要が変わりました。詳細脚本を依頼し直してください。')
        if request['kind'] in ('evaluation','improvement') and request['context']['source_story']!=latest['story']:
            raise ValueError('依頼の元脚本が現在の版と一致しません。依頼を作り直してください。')
        if request['kind'] == 'improvement':
            evaluation = exact_evaluation(self.editorial.load(), latest)
            if not evaluation or evaluation['id'] != request['evaluation_id']:
                raise ValueError('改善元の評価が古くなりました。最新評価で依頼し直してください。')
        normalized = normalize_response(payload, request)
        if any(r['payload'] == normalized and r['status'] == '保存済み' for r in data['responses']):
            raise ValueError('この回答は保存済みです。新しい依頼で次のサイクルを開始してください。')
        return request, normalized

    def queue_plan(self, raw):
        """GitHub回答を未採用の受信箱へ登録する計画。正式作品は変更しない。"""
        data = self.concepts.load()
        request, payload = self.validate_response(raw, data)
        if any(r['payload'] == payload for r in data['responses']):
            raise ValueError('この回答候補は登録済みです。')
        data['responses'].append(record(request_id=request['id'], payload=payload, status='確認待ち'))
        data['revision'] += 1
        validate_concepts(data, self.project_id)
        content=json_bytes(data)
        if len(content)>MAX_JSON_BYTES:raise ValueError('回答候補を含む制作履歴が8MiBを超えます。削除せず登録を停止しました。')
        return content

    def canonical_plan(self, operation):
        """既存保存関数を一時コピーで実行し、差分だけを本保存へ渡す。"""
        names = ('story_history.json', 'editorial.json')
        originals = {n: self.transactions.target(n).read_bytes() for n in names if self.transactions.target(n).exists()}
        with TemporaryDirectory(prefix='manga-story-studio-') as temp:
            root = Path(temp)/self.project_id
            root.mkdir()
            for name, content in originals.items():
                (root/name).write_bytes(content)
            result = operation(StoryStore(temp, self.project_id), EditorialStore(temp, self.project_id))
            updates = {n: (root/n).read_bytes() for n in names if (root/n).exists() and (root/n).read_bytes() != originals.get(n)}
            return result, updates

    def import_response(self, raw, *, expected_revision):
        with self.locked(expected_revision) as data:
            request, payload = self.validate_response(raw, data)
            kind, updates = request['kind'], {}
            if kind == 'concepts':
                result = record(request_id=request['id'], payload=payload)
                data['batches'].append(result)
            elif kind == 'detail':
                result, updates = self.canonical_plan(lambda stories, ed: stories.save(payload['story'], expected_version_id=request['base_version_id']))
                data['details'].append(record(request_id=request['id'], version_id=result['id'], selection_id=request['selection_id']))
            elif kind == 'evaluation':
                result, updates = self.canonical_plan(lambda stories, ed: ed.save_evaluation(request['base_version_id'], json.dumps(payload['evaluation'], ensure_ascii=False), expected_revision=ed.load()['revision']))
                data['evaluations'].append(record(request_id=request['id'], version_id=request['base_version_id'], evaluation_id=result['id'],
                                                  reference_usage=payload['reference_usage'], contradictions=payload['contradictions']))
            else:
                options = request['options']
                chain = lineage(data, self.stories.load_history(), request['base_version_id'])
                if chain['cycles'] >= cycle_limit(data, chain['root_version_id']):
                    raise ValueError('改善上限です。追加改善を明示承認してください。')
                result_pair, updates = self.canonical_plan(lambda stories, ed: ed.save_improvement(json.dumps(payload['story'], ensure_ascii=False),
                    request['base_version_id'], request['evaluation_id'], options['mode'], options['criteria'], options['targets'], options['reason'], expected_version_id=request['base_version_id']))
                result = result_pair[0]
                from manga.story_comparison import compare_stories
                data['improvements'].append(record(request_id=request['id'], version_id=result['id'], evaluation_id=request['evaluation_id'],
                    base_version_id=request['base_version_id'], reason=options['reason'], changes=compare_stories(request['context']['source_story'], result['story'])))
            inbox = next((r for r in data['responses'] if r['payload'] == payload), None)
            if inbox:
                inbox['status'] = '保存済み'
            else:
                data['responses'].append(record(request_id=request['id'], payload=payload, status='保存済み'))
            data['events'].append(record(kind='import', request_id=request['id'], result_id=result['id']))
            self.save(data, updates)
            return result

    def set_policy(self, target_score, improvement_limit, *, expected_revision):
        with self.locked(expected_revision) as data:
            data['events'].append(record(kind='policy', before=copy.deepcopy(data['policy'])))
            data['policy'] = dict(target_score=target_score, improvement_limit=improvement_limit)
            self.save(data)

    def save_choices(self, choices, *, expected_revision):
        with self.locked(expected_revision) as data:
            select_techniques(self.editorial.load(), choices)
            data['reference_choices'] = copy.deepcopy(choices)
            self.save(data)

    def pause(self, *, expected_revision, expected_version_id):
        with self.locked(expected_revision) as data:
            history = self.stories.load_history()
            if not history:
                raise ValueError('脚本がありません。')
            if history[-1]['id']!=expected_version_id:raise SyncConflict('表示後に脚本が変更されました。画面を更新してください。')
            data['events'].append(record(kind='pause', version_id=history[-1]['id'], reason='利用者が追加改善せず最終確認へ進むことを選択'))
            self.save(data)

    def restore(self, version_id, *, expected_revision, expected_version_id):
        with self.locked(expected_revision) as data:
            history = self.stories.load_history()
            if not history:
                raise ValueError('復元できる脚本がありません。')
            if history[-1]['id']!=expected_version_id:raise SyncConflict('表示後に脚本が変更されました。画面を更新して復元元を確認してください。')
            result, updates = self.canonical_plan(lambda stories, ed: stories.restore(version_id, expected_version_id=history[-1]['id']))
            data['events'].append(record(kind='restore', version_id=result['id'], restored_from=version_id))
            self.save(data, updates)
            return result

    def extend(self, additional, reason, *, expected_revision, expected_version_id):
        if type(additional) is not int or not 1 <= additional <= 20:
            raise ValueError('追加改善は1〜20回で指定してください。')
        reason = text(reason, '追加改善の理由', 2000, True)
        with self.locked(expected_revision) as data:
            history = self.stories.load_history()
            if not history:
                raise ValueError('脚本がありません。')
            if history[-1]['id']!=expected_version_id:raise SyncConflict('表示後に脚本が変更されました。画面を更新してください。')
            chain = lineage(data, history, history[-1]['id'])
            limit = max(chain['cycles'], cycle_limit(data, chain['root_version_id'])) + additional
            if limit>100:raise ValueError('追加承認を含めた改善回数は100回までです。')
            data['extensions'].append(record(root_version_id=chain['root_version_id'], limit=limit, reason=reason))
            self.save(data)

    def finalize(self, *, expected_revision, expected_version_id, expected_evaluation_id, reviewed=False, accept_remaining=False):
        if reviewed is not True:
            raise ValueError('作品設定・独自性・変更箇所を人間が最終確認してください。')
        with self.locked(expected_revision) as data:
            history = self.stories.load_history()
            if not history:
                raise ValueError('脚本がありません。')
            latest, editorial = history[-1], self.editorial.load()
            if latest['id']!=expected_version_id:raise SyncConflict('表示後に脚本が変更されました。最新の版を最終確認してください。')
            evaluation = exact_evaluation(editorial, latest)
            if evaluation is None:
                raise ValueError('最新の版を評価してから最終確認してください。')
            if evaluation['id']!=expected_evaluation_id:raise SyncConflict('表示後に評価が更新されました。最新評価を確認してください。')
            warnings = regression_notes(data, editorial, history, latest)+stale_references(references_for_version(data,history,latest['id']),editorial)
            if (warnings or evaluation['payload']['total_score'] < data['policy']['target_score']) and accept_remaining is not True:
                raise ValueError('目標未達・劣化・要確認事項を確認し、残る課題を承認してください。')
            result, updates = self.canonical_plan(lambda stories, ed: ed.finalize(expected_version_id=latest['id'], expected_revision=ed.load()['revision']))
            data['finalizations'].append(record(version_id=latest['id'], evaluation_id=evaluation['id'],
                                                reviewed=True, accept_remaining=accept_remaining, warnings=warnings))
            self.save(data, updates)
            return result
