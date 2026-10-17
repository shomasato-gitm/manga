"""品質データだけをロック・競合検出・原子的保存で更新し、変更履歴を残す。"""
import copy
import fcntl
import json
from pathlib import Path
from uuid import uuid4

from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.review_models import empty_review, validate_review, MANUAL_ITEMS, MANUAL_STATES, TASK_STATES, target, issue_valid, text
from manga.review_context import load_context, page_current
from manga.review_status import page_status, latest_scan, task_status


class ReviewStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.root = Path(directory) / project_id
        self.path = self.root / 'review.json'

    def load(self):
        try:
            return validate_review(json.loads(self.path.read_text(encoding='utf-8')), self.project_id)
        except FileNotFoundError:
            return empty_review(self.project_id)
        except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError) as error:
            raise StorageError('品質レビューを読み込めません。review.jsonを上書きせずバックアップと形式を確認してください。') from error

    def _update(self, action, expected_revision, snapshot):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / '.review.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    data = self.load()
                    if type(expected_revision) is not int or data['revision'] != expected_revision:
                        raise StorageError('品質レビューが別画面で更新されました。入力を控え、画面を再読み込みして保存し直してください。')
                    current = load_context(self.root.parent, self.project_id, cached=False)
                    if current.snapshot['token'] != snapshot['token']:
                        raise StorageError('制作データが変更されました。提案や入力は自動適用していません。最新データを再読み込み・再確認してください。')
                    action(data, current)
                    data['revision'] += 1
                    # 書き込み直前にも確認する。他の制作機能のロックを逆順取得しない。
                    if load_context(self.root.parent, self.project_id, cached=False).snapshot['token'] != snapshot['token']:
                        raise StorageError('保存中に制作データが変わりました。再読み込みしてください。')
                    validate_review(data, self.project_id)
                    write_json_atomic(self.path, data)
                    return data
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError as error:
            raise StorageError('品質レビューを保存できません。既存データは保持しています。保存先の権限と空き容量を確認して再試行してください。') from error

    @staticmethod
    def _event(data, kind, before=None, **fields):
        data['events'].append(dict(id=str(uuid4()), created_at=now_iso(), kind=kind, before=copy.deepcopy(before), **fields))

    def save_scan(self, result, *, expected_revision):
        def action(data, context):
            if result['snapshot']['token'] != context.snapshot['token']: raise ValueError('検査対象が古くなっています。')
            data['scans'].append(copy.deepcopy(result))
        return self._update(action, expected_revision, result['snapshot'])

    def save_manual(self, page, answers, notes, *, expected_revision, snapshot):
        target(dict(page=page, panel=None))
        if set(answers) != set(MANUAL_ITEMS) or any(v not in MANUAL_STATES for v in answers.values()):
            raise ValueError('すべての手動確認項目を指定してください。')
        text(notes, '確認メモ', required=False)
        def action(data, context):
            target(dict(page=page, panel=None), context.pages)
            key = str(page)
            self._event(data, 'manual', data['manual'].get(key), page=page)
            data['manual'][key] = dict(answers=copy.deepcopy(answers), notes=notes, snapshot=copy.deepcopy(snapshot), updated_at=now_iso())
            # 回答変更後は明示的な最終確認をもう一度必要とする。
            if key in data['finals']:
                self._event(data, 'final_invalidated', data['finals'].pop(key), page=page)
        return self._update(action, expected_revision, snapshot)

    def confirm_preview(self, page, *, expected_revision, snapshot):
        def action(data, context):
            target(dict(page=page, panel=None), context.pages)
            state = page_status(data, snapshot, page)
            if not state['scan_done'] or state['blockers']:
                raise ValueError('最新データの自動チェックと描画を成功させてからプレビューを確認してください。')
            self._event(data, 'preview', data['previews'].get(str(page)), page=page)
            data['previews'][str(page)] = dict(snapshot=copy.deepcopy(snapshot), updated_at=now_iso())
        return self._update(action, expected_revision, snapshot)

    def finalize(self, page, *, expected_revision, snapshot):
        def action(data, context):
            target(dict(page=page, panel=None), context.pages)
            state = page_status(data, snapshot, page)
            if not state['preview'] or not state['manual_done'] or not state['scan_done'] or state['blockers'] or state['tasks']:
                raise ValueError('このページの自動検査・プレビュー・手動確認と修正タスクの再確認を終えてください。未確認のままでも警告付き書き出しは可能です。')
            self._event(data, 'final', data['finals'].get(str(page)), page=page)
            data['finals'][str(page)] = dict(snapshot=copy.deepcopy(snapshot), updated_at=now_iso())
        return self._update(action, expected_revision, snapshot)

    def create_task(self, issue, *, origin, expected_revision, snapshot, source_id=None):
        issue_valid(issue)
        if origin not in ('auto', 'manual', 'ai'): raise ValueError('問題の出典が不正です。')
        def action(data, context):
            if issue['page'] is not None: target(issue, context.pages)
            if any(t['issue']['id'] == issue['id'] and task_status(t, snapshot) not in ('確認完了', '対応不要') for t in data['tasks']):
                raise ValueError('この問題には未解決タスクがあります。既存タスクを更新してください。')
            discovered = snapshot
            if origin == 'auto':
                source = next((s for s in reversed(data['scans']) if (source_id is None or s['id'] == source_id) and any(i == issue for i in s['issues'])), None)
                if source is None: raise ValueError('タスクの元となる自動検査結果がありません。')
                discovered = source['snapshot']
            elif origin == 'ai':
                review = next((r for r in data['ai_reviews'] if r['id'] == source_id and issue in r['issues']), None)
                if review is None: raise ValueError('タスクの元となるAI指摘がありません。')
                request = next(r for r in data['ai_requests'] if r['id'] == review['request_id'])
                discovered = request['snapshot']
            data['tasks'].append(dict(id=str(uuid4()), created_at=now_iso(), updated_at=now_iso(),
                issue=copy.deepcopy(issue), origin=origin, source_id=source_id, method=issue['suggestion'],
                status='未対応', snapshot=copy.deepcopy(discovered), verified_snapshot=None))
            if origin == 'ai':
                previous = next((r for r in data['ai_reviews'] if r['id'] == source_id), None)
                self._event(data, 'ai_decision', previous, review_id=source_id)
                self._decide(data, source_id, issue['id'], 'タスク化')
        return self._update(action, expected_revision, snapshot)

    def update_task(self, task_id, status, method, *, expected_revision, snapshot, rechecked=False):
        validate_id(task_id); text(method, '修正方法', required=False)
        if status not in TASK_STATES or type(rechecked) is not bool: raise ValueError('修正状態が不正です。')
        def action(data, context):
            task = next((t for t in data['tasks'] if t['id'] == task_id), None)
            if task is None: raise ValueError('修正タスクが見つかりません。')
            if status == '確認完了':
                if task_status(task, snapshot) not in ('修正済み', '再確認待ち') or not rechecked:
                    raise ValueError('修正後に再確認待ちへ進め、現在の画像・設定を人間が再確認してください。')
                if task['issue']['page'] is not None: target(task['issue'], context.pages)
            self._event(data, 'task', task, task_id=task_id)
            task.update(status=status, method=method, updated_at=now_iso())
            if status == '確認完了': task['verified_snapshot'] = copy.deepcopy(snapshot)
        return self._update(action, expected_revision, snapshot)

    def save_request(self, request, *, expected_revision):
        def action(data, context):
            for item in request['targets']: target(item, context.pages)
            if request['snapshot']['token'] != context.snapshot['token']: raise ValueError('依頼対象が古くなっています。')
            data['ai_requests'].append(copy.deepcopy(request))
        return self._update(action, expected_revision, request['snapshot'])

    def import_ai(self, raw, *, expected_revision, snapshot):
        from manga.review_prompts import parse_ai_review
        def action(data, context):
            result = parse_ai_review(raw, data['ai_requests'], context)
            if any(r['request_id'] == result['request_id'] and r['issues'] == result['issues'] for r in data['ai_reviews']):
                raise ValueError('同じAIレビューは取り込み済みです。')
            data['ai_reviews'].append(result)
        return self._update(action, expected_revision, snapshot)

    @staticmethod
    def _decide(data, review_id, issue_id, decision):
        review = next((r for r in data['ai_reviews'] if r['id'] == review_id), None)
        if review is None or issue_id not in review['decisions']: raise ValueError('AIレビューの指摘がありません。')
        review['decisions'][issue_id] = decision

    def decide_ai(self, review_id, issue_id=None, *, expected_revision, snapshot):
        def action(data, context):
            review = next((r for r in data['ai_reviews'] if r['id'] == review_id), None)
            if review is None: raise ValueError('AIレビューがありません。')
            self._event(data, 'ai_decision', review, review_id=review_id)
            if issue_id is None: review['reviewed'] = True
            else: self._decide(data, review_id, issue_id, '対応不要')
        return self._update(action, expected_revision, snapshot)
