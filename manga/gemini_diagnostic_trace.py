"""固定文診断だけの段階記録。本文・認証・例外文字列は受け取らない。"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo
import json

from manga.models import validate_id, now_iso
from manga.storage import StorageError

STAGES = {
    'pre_send': '通信開始前・予算実行権の準備',
    'sending': '送信処理（Googleへの到達は未確定）',
    'http_response': 'HTTP応答の受信',
    'response_validation': '応答形式・モデル・利用量の検証',
    'result_storage': '診断結果ファイルの保存',
    'budget_settlement': '利用量の計算・予算精算',
    'history_storage': '診断結果の履歴保存',
}
REASONS = {'unauthorized', 'forbidden', 'not_found', 'rate_limited', 'bad_request',
           'server_error', 'network_error', 'connection_timeout', 'response_timeout',
           'invalid_response', 'http_error', 'storage_error', 'budget_error', 'interrupted'}
_sink = ContextVar('gemini_diagnostic_trace', default=None)


class DiagnosticStorageError(OSError):
    pass


@contextmanager
def capture(trace):
    token = _sink.set(trace)
    try:
        yield
    finally:
        _sink.reset(token)


def emit(stage, outcome, *, reason=None, http_status=None):
    trace = _sink.get()
    if trace is not None:
        trace.record(stage, outcome, reason=reason, http_status=http_status)


class Trace:
    def __init__(self, service, identifier):
        self.service = service
        self.identifier = validate_id(identifier)
        self.last = None
        self.sequence = 0

    def record(self, stage, outcome, *, reason=None, http_status=None):
        if (stage not in STAGES or outcome not in ('started', 'completed', 'failed')
                or (reason is not None and reason not in REASONS)
                or (http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599))):
            raise ValueError('診断段階の固定値が不正です。')
        item = (stage, outcome, reason, http_status)
        if self.last == item:
            return
        try:
            value = dict(diagnosis_id=self.identifier, stage=stage, outcome=outcome,
                         reason=reason, http_status=http_status, created_at=now_iso())
            self.service.store.write(f'story_ai/diagnostics/{self.identifier}.trace/{self.sequence+1:04d}.stage',
                                     json.dumps(value, allow_nan=False).encode())
        except (OSError, StorageError, ValueError):
            raise DiagnosticStorageError('診断段階を保存できません。保存先・容量と請求を確認してください。再送しません。') from None
        self.last = item
        self.sequence += 1

    def failed(self, reason):
        # 元の通信エラーを、追加ログの保存失敗で隠さない。
        stage = self.last[0] if self.last else 'pre_send'
        if self.last and self.last[2] == reason:
            return
        try:
            self.record(stage, 'failed', reason=reason)
        except DiagnosticStorageError:
            pass


def history(service, identifier):
    """旧履歴に段階を補完しない。保存済みの安全な固定値だけを返す。"""
    validate_id(identifier)
    result = []
    root = service.store.safe(f'story_ai/diagnostics/{identifier}.trace')
    if not root.exists():
        return result
    paths = sorted(root.iterdir())
    if len(paths) > 64:
        raise ValueError('診断段階ログの件数が不正です。')
    for index, path in enumerate(paths, 1):
        if path.name != f'{index:04d}.stage':
            raise ValueError('診断段階ログの順序を確認できません。')
        try:
            event = json.loads(service.store.read(str(path.relative_to(service.store.root))))
            if set(event) != {'diagnosis_id','stage','outcome','reason','http_status','created_at'} or event['diagnosis_id'] != identifier:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError('診断段階ログを安全に読み込めません。') from None
        stage, outcome, reason, code = (event.get(k) for k in ('stage', 'outcome', 'reason', 'http_status'))
        if (stage not in STAGES or outcome not in ('started', 'completed', 'failed')
                or (reason is not None and reason not in REASONS)
                or (code is not None and (type(code) is not int or not 100 <= code <= 599))):
            raise ValueError('診断段階ログの形式が不正です。記録を書き換えずバックアップを確認してください。')
        try:
            value = datetime.fromisoformat(event['created_at'])
            if value.tzinfo is None:raise ValueError
            at = value.astimezone(ZoneInfo('Asia/Tokyo')).isoformat()
        except (ValueError, TypeError):
            raise ValueError('診断段階の日時を確認できません。') from None
        result.append(dict(stage=stage, outcome=outcome, reason=reason, http_status=code, created_at=at))
    return result
