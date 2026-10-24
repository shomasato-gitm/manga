"""SQLite-based, fail-closed per-work and monthly AI API spending ledger.

Values are stored as INTEGER millionths of a JPY, never binary floats.
The reservation protects against concurrent jobs; an uncertain provider
outcome deliberately retains the reservation until a person reconciles it.
A provider can charge more than the estimated maximum: this is a software
preflight guard, NOT a guarantee of the external invoice amount.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import uuid4

from manga.models import validate_id
from .integrity import BudgetStateError, policy_json, initialize, validate, validate_quote, blockers, monthly_where

_JPY_SCALE = Decimal("1000000")
_TOKYO = ZoneInfo("Asia/Tokyo")
_STATES = ("reserved", "uncertain", "settled", "cancelled")


class BudgetExceeded(ValueError):
    """The next provider call would exceed at least one configured limit."""


class ReservationConflict(ValueError):
    """A request ID was reused for another operation, or is in the wrong state."""


def _yen_to_units(value, *, positive=False):
    try:
        raw = str(value)
        if len(raw)>128:
            raise ValueError
        amount = Decimal(raw)
        if not amount.is_finite() or amount < 0 or (positive and amount == 0):
            raise ValueError
        if amount > Decimal('9000000000'):
            raise ValueError
        with localcontext() as context:
            context.prec = max(40, len(amount.as_tuple().digits)+8)
            units = 1 if 0 < amount < Decimal('0.000001') else int((amount * _JPY_SCALE).to_integral_value(rounding=ROUND_CEILING))
        return units
    except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("金額は0以上の有限な数値で指定してください。") from exc


def _units_to_yen(value):
    return Decimal(value) / _JPY_SCALE


def _check_label(label, name):
    if not isinstance(label, str) or not label.strip() or len(label) > 128:
        raise ValueError(f"{name}が不正です。")
    return label.strip()


def _now_and_month(now=None):
    now = datetime.now(_TOKYO) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("日時はタイムゾーン付きで指定してください。")
    local = now.astimezone(_TOKYO)
    return local.isoformat(), local.strftime("%Y-%m")


class BudgetLedger:
    """Use a shared DB path for all jobs: atomic reservations prevent overspend.

    project_limit_jpy: lifetime cap for one work, independent of calendar month.
    monthly_limit_jpy: shared calendar-month cap for all works in this ledger.
    provider_monthly_limits_jpy: optional further limits per provider.
    """

    def __init__(
        self, db_path, *,
        project_limit_jpy="500",
        monthly_limit_jpy="5000",
        provider_monthly_limits_jpy=None,
    ):
        self.path = Path(db_path)
        self.marker = self.path.with_name(self.path.name+'.initialized')
        self.project_limit = _yen_to_units(project_limit_jpy)
        self.monthly_limit = _yen_to_units(monthly_limit_jpy)
        self.provider_limits = {
            _check_label(name, "プロバイダー"): _yen_to_units(limit)
            for name, limit in (provider_monthly_limits_jpy or {}).items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction(initializing=True) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and 'api_budget_requests' not in tables:
                raise BudgetStateError('予算台帳のテーブルがありません。既存DBを空の台帳として扱いません。')
            if self.marker.exists() and 'api_budget_requests' not in tables:
                raise BudgetStateError('初期化済みの予算台帳が空です。自動リセットを停止しました。')
            db.execute("""CREATE TABLE IF NOT EXISTS api_budget_requests (
                request_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                month_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                kind TEXT NOT NULL,
                quote_units INTEGER NOT NULL CHECK(quote_units > 0),
                actual_units INTEGER,
                state TEXT NOT NULL CHECK(state IN ('reserved','uncertain','settled','cancelled')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_budget_project ON api_budget_requests(project_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_budget_month ON api_budget_requests(month_key, provider)")
            initialize(db, self._policy())
        try:
            descriptor = os.open(self.marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor,'w') as marker:
                marker.write('Existing budget history must not be reset.\n')
                marker.flush();os.fsync(marker.fileno())

    def _policy(self):
        return policy_json(self.project_limit, self.monthly_limit, self.provider_limits)

    @contextmanager
    def _transaction(self, initializing=False):
        # Normal operations must not silently create an empty DB after file loss.
        if self.path.is_symlink() or self.marker.is_symlink():
            raise BudgetStateError('予算台帳・初期化記録へのシンボリックリンクは使用できません。')
        readonly_creation = not initializing or self.marker.exists()
        path = self.path.resolve().as_uri()+'?mode=rw' if readonly_creation else str(self.path)
        db = sqlite3.connect(path, uri=readonly_creation, timeout=15.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("BEGIN IMMEDIATE")
            if not initializing:
                validate(db, self._policy())
            yield db
            db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _used(db, where="", params=()):
        """Reserved and uncertain requests count at their full quotation."""
        query = """SELECT COALESCE(SUM(CASE
            WHEN state = 'settled' THEN actual_units
            WHEN state IN ('reserved', 'uncertain') THEN quote_units
            ELSE 0 END), 0)
            FROM api_budget_requests"""
        if where:
            query += " WHERE " + where
        return int(db.execute(query, params).fetchone()[0])

    @staticmethod
    def _get(db, request_id):
        return db.execute(
            """SELECT project_id, month_key, provider, model, kind, quote_units,
                      actual_units, state FROM api_budget_requests WHERE request_id=?""",
            (request_id,),
        ).fetchone()

    def reserve(self, *, project_id, request_id, provider, model, kind, max_jpy, now=None, evidence=None):
        """Reserve the *maximum* possible charge BEFORE starting network I/O."""
        validate_id(project_id)
        request_id = _check_label(request_id, "リクエストID")
        provider = _check_label(provider, "プロバイダー")
        model = _check_label(model, "モデル")
        kind = _check_label(kind, "用途")
        quote = _yen_to_units(max_jpy, positive=True)
        at, month = _now_and_month(now)
        evidence_json = json.dumps(evidence, sort_keys=True, allow_nan=False) if evidence is not None else None
        if evidence is not None:
            validate_quote(evidence,quote,provider,model,current=True,now=now)

        with self._transaction() as db:
            row = self._get(db, request_id)
            if row:
                same = (row[0], row[2], row[3], row[4], row[5]) == (
                    project_id, provider, model, kind, quote
                )
                if not same:
                    raise ReservationConflict("リクエストIDが別の処理で使用済みです。")
                old = db.execute('SELECT evidence FROM api_budget_quotes WHERE request_id=?', (request_id,)).fetchone()
                if (old[0] if old else None) != evidence_json:
                    raise ReservationConflict('同じリクエストIDの見積根拠が異なります。')
                return "already_" + row[7]  # idempotent; do not call provider twice

            self._assert_open(db)
            checks = [
                ("作品", self.project_limit, self._used(db, "project_id=?", (project_id,))),
                ("月間", self.monthly_limit, self._used(db, monthly_where(), (month,))),
            ]
            if provider in self.provider_limits:
                checks.append((
                    "プロバイダー月間", self.provider_limits[provider],
                    self._used(db, monthly_where(True), (month, provider)),
                ))
            for label, limit, used in checks:
                if used + quote > limit:
                    raise BudgetExceeded(f"{label}API予算の残額が不足しています。")

            db.execute(
                """INSERT INTO api_budget_requests
                (request_id, project_id, month_key, provider, model, kind, quote_units,
                 actual_units, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'reserved', ?, ?)""",
                (request_id, project_id, month, provider, model, kind, quote, at, at),
            )
            if evidence_json is not None:
                db.execute('INSERT INTO api_budget_quotes VALUES (?,?)', (request_id,evidence_json))
            return "reserved"

    @staticmethod
    def _assert_open(db):
        uncertain, overrun = blockers(db)
        if uncertain or overrun:
            raise BudgetExceeded('請求不明または未確認の見積超過があります。明細の手動確認まで新しいAPI処理を停止します。')

    def claim_execution(self, request_id, *, now=None):
        """Durable single execution claim, immediately before the provider call."""
        request_id = _check_label(request_id, 'リクエストID')
        at, month = _now_and_month(now)
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None or row[7]!='reserved' or db.execute('SELECT 1 FROM api_budget_executions WHERE request_id=?',(request_id,)).fetchone():
                raise ReservationConflict('このリクエストは実行済み、または実行権を取得できません。')
            evidence = db.execute('SELECT evidence FROM api_budget_quotes WHERE request_id=?',(request_id,)).fetchone()
            if not evidence:
                raise ValueError('実行には料金表と最大利用量の見積根拠が必要です。')
            validate_quote(json.loads(evidence[0]),row[5],row[2],row[3],current=True,now=now)
            self._assert_open(db)
            if self._used(db,'project_id=?',(row[0],))>self.project_limit or self._used(db,monthly_where(),(month,))>self.monthly_limit:
                raise BudgetExceeded('実行前に共有予算が不足しました。送信せず停止します。')
            if row[2] in self.provider_limits and self._used(db,monthly_where(True),(month,row[2]))>self.provider_limits[row[2]]:
                raise BudgetExceeded('実行前にプロバイダーの月間予算が不足しました。')
            claim_id = str(uuid4())
            db.execute('INSERT INTO api_budget_executions VALUES (?,?,?)',(request_id,at,claim_id))
            return claim_id

    def mark_uncertain(self, request_id):
        """A network timeout/ambiguous result must NOT release the reservation."""
        request_id = _check_label(request_id, "リクエストID")
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None or row[7] not in ("reserved", "uncertain"):
                raise ReservationConflict("課金不明の処理として記録できません。")
            db.execute(
                "UPDATE api_budget_requests SET state='uncertain', updated_at=? WHERE request_id=?",
                (_now_and_month()[0], request_id),
            )

    def settle(self, request_id, *, actual_jpy, charged_at=None, execution_claim=None, confirmed_charge=False):
        """Account for reported usage; an overrun blocks subsequent reservations."""
        request_id = _check_label(request_id, "リクエストID")
        actual = _yen_to_units(actual_jpy)
        at, month = _now_and_month(charged_at)
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None:
                raise ReservationConflict("精算対象のリクエストがありません。")
            if row[7] == "settled" and row[6] == actual:
                return
            if row[7] not in ("reserved", "uncertain"):
                raise ReservationConflict("この状態のリクエストは精算できません。")
            claim = db.execute('SELECT claim_id FROM api_budget_executions WHERE request_id=?',(request_id,)).fetchone()
            owned = claim is not None and execution_claim == claim[0]
            if (claim and not owned and row[7]=='reserved') or (row[7]=='uncertain' and not owned and confirmed_charge is not True):
                raise ReservationConflict('実行中の予約は第三者が精算できません。請求不明分は明細の手動確認が必要です。')
            db.execute(
                """UPDATE api_budget_requests SET state='settled',
                   actual_units=?, updated_at=?, month_key=? WHERE request_id=?""",
                (actual, at, month, request_id),
            )

    def cancel_not_sent(self, request_id):
        """Only safe when we KNOW the provider was never called."""
        request_id = _check_label(request_id, "リクエストID")
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None or row[7] != "reserved":
                raise ReservationConflict("送信済み・課金不明の予約は自動解除できません。")
            if db.execute('SELECT 1 FROM api_budget_executions WHERE request_id=?',(request_id,)).fetchone():
                raise ReservationConflict('実行権を取得済みです。送信前と仮定して解除できません。')
            db.execute(
                "UPDATE api_budget_requests SET state='cancelled', updated_at=? WHERE request_id=?",
                (_now_and_month()[0], request_id),
            )

    def reconcile_no_charge(self, request_id, *, confirmed_no_charge=False):
        """Explicit operator verification; never called by background retries."""
        if confirmed_no_charge is not True:
            raise ValueError("請求が発生していないことの明示確認が必要です。")
        request_id = _check_label(request_id, "リクエストID")
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None or row[7] != "uncertain":
                raise ReservationConflict("課金不明のリクエストだけを確認解除できます。")
            db.execute(
                """UPDATE api_budget_requests SET state='settled',
                    actual_units=0, updated_at=? WHERE request_id=?""",
                (_now_and_month()[0], request_id),
            )

    def summary(self, project_id, *, now=None):
        validate_id(project_id)
        _, month = _now_and_month(now)
        with self._transaction() as db:
            work = self._used(db, "project_id=?", (project_id,))
            monthly = self._used(db, monthly_where(), (month,))
            uncertain, overrun = blockers(db)
            pending = db.execute("SELECT count(*) FROM api_budget_requests WHERE state IN ('reserved','uncertain')").fetchone()[0]
            providers = {name:dict(limit_jpy=_units_to_yen(limit), used_jpy=_units_to_yen(self._used(db,monthly_where(True),(month,name)))) for name,limit in self.provider_limits.items()}
            groups = db.execute(
                """SELECT state, count(*) FROM api_budget_requests
                   WHERE project_id=? GROUP BY state""", (project_id,),
            ).fetchall()
            project_settled = db.execute("SELECT COALESCE(SUM(actual_units),0) FROM api_budget_requests WHERE project_id=? AND state='settled'",(project_id,)).fetchone()[0]
            month_settled = db.execute("SELECT COALESCE(SUM(actual_units),0) FROM api_budget_requests WHERE month_key=? AND state='settled'",(month,)).fetchone()[0]
        return {
            "project_limit_jpy": _units_to_yen(self.project_limit),
            "project_used_jpy": _units_to_yen(work),
            "project_settled_jpy": _units_to_yen(project_settled),
            "project_reserved_jpy": _units_to_yen(work-project_settled),
            "project_remaining_jpy": _units_to_yen(max(0, self.project_limit - work)),
            "monthly_limit_jpy": _units_to_yen(self.monthly_limit),
            "monthly_used_jpy": _units_to_yen(monthly),
            "monthly_settled_jpy": _units_to_yen(month_settled),
            "monthly_reserved_jpy": _units_to_yen(monthly-month_settled),
            "monthly_remaining_jpy": _units_to_yen(max(0, self.monthly_limit - monthly)),
            "requests_by_status": dict(groups),
            "month": month,
            "monthly_pending_count": pending,
            "uncertain_count": uncertain,
            "unreviewed_overrun_count": overrun,
            "blocked": bool(uncertain or overrun),
            "providers": providers,
        }

    def request_status(self, request_id):
        """Read-only recovery lookup. This never retries, settles or releases money."""
        request_id=_check_label(request_id,'リクエストID')
        with self._transaction() as db:
            row=self._get(db,request_id)
            if row is None:return None
            return dict(project_id=row[0],provider=row[2],model=row[3],kind=row[4],
                        quote_jpy=_units_to_yen(row[5]),actual_jpy=_units_to_yen(row[6]) if row[6] is not None else None,state=row[7])

    def acknowledge_overrun(self, request_id, *, confirmed_reviewed=False, reason=''):
        """Operator-only acknowledgement, preserving the actual charge and audit trail."""
        if confirmed_reviewed is not True or not isinstance(reason,str) or not reason.strip() or len(reason)>1000:
            raise ValueError('超過請求と料金見積の原因を手動確認し、理由を明記してください。')
        request_id = _check_label(request_id,'リクエストID')
        with self._transaction() as db:
            row = self._get(db,request_id)
            if row is None or row[7]!='settled' or row[6]<=row[5]:
                raise ReservationConflict('見積超過の精算記録ではありません。')
            if db.execute('SELECT 1 FROM api_budget_overrun_reviews WHERE request_id=?',(request_id,)).fetchone():
                raise ReservationConflict('この見積超過は確認済みです。記録は上書きしません。')
            db.execute('INSERT INTO api_budget_overrun_reviews VALUES (?,?,?)',(request_id,reason.strip(),_now_and_month()[0]))
