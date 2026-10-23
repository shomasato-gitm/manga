"""SQLite-based, fail-closed per-work and monthly AI API spending ledger.

Values are stored as INTEGER millionths of a JPY, never binary floats.
The reservation protects against concurrent jobs; an uncertain provider
outcome deliberately retains the reservation until a person reconciles it.
A provider can charge more than the estimated maximum: this is a software
preflight guard, NOT a guarantee of the external invoice amount.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo

from manga.models import validate_id

_JPY_SCALE = Decimal("1000000")
_TOKYO = ZoneInfo("Asia/Tokyo")
_STATES = ("reserved", "uncertain", "settled", "cancelled")


class BudgetExceeded(ValueError):
    """The next provider call would exceed at least one configured limit."""


class ReservationConflict(ValueError):
    """A request ID was reused for another operation, or is in the wrong state."""


def _yen_to_units(value, *, positive=False):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or (positive and amount == 0):
            raise ValueError
        units = int((amount * _JPY_SCALE).to_integral_value(rounding=ROUND_CEILING))
        if units > 9_000_000_000_000_000:
            raise ValueError
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
    now = now or datetime.now(_TOKYO)
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
        self.project_limit = _yen_to_units(project_limit_jpy)
        self.monthly_limit = _yen_to_units(monthly_limit_jpy)
        self.provider_limits = {
            _check_label(name, "プロバイダー"): _yen_to_units(limit)
            for name, limit in (provider_monthly_limits_jpy or {}).items()
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
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

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
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

    def reserve(self, *, project_id, request_id, provider, model, kind, max_jpy, now=None):
        """Reserve the *maximum* possible charge BEFORE starting network I/O."""
        validate_id(project_id)
        request_id = _check_label(request_id, "リクエストID")
        provider = _check_label(provider, "プロバイダー")
        model = _check_label(model, "モデル")
        kind = _check_label(kind, "用途")
        quote = _yen_to_units(max_jpy, positive=True)
        at, month = _now_and_month(now)

        with self._transaction() as db:
            row = self._get(db, request_id)
            if row:
                same = (row[0], row[2], row[3], row[4], row[5]) == (
                    project_id, provider, model, kind, quote
                )
                if not same:
                    raise ReservationConflict("リクエストIDが別の処理で使用済みです。")
                return "already_" + row[7]  # idempotent; do not call provider twice

            checks = [
                ("作品", self.project_limit, self._used(db, "project_id=?", (project_id,))),
                ("月間", self.monthly_limit, self._used(db, "month_key=?", (month,))),
            ]
            if provider in self.provider_limits:
                checks.append((
                    "プロバイダー月間", self.provider_limits[provider],
                    self._used(db, "month_key=? AND provider=?", (month, provider)),
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
            return "reserved"

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

    def settle(self, request_id, *, actual_jpy):
        """Account for reported usage; an overrun blocks subsequent reservations."""
        request_id = _check_label(request_id, "リクエストID")
        actual = _yen_to_units(actual_jpy)
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None:
                raise ReservationConflict("精算対象のリクエストがありません。")
            if row[7] == "settled" and row[6] == actual:
                return
            if row[7] not in ("reserved", "uncertain"):
                raise ReservationConflict("この状態のリクエストは精算できません。")
            db.execute(
                """UPDATE api_budget_requests SET state='settled',
                   actual_units=?, updated_at=? WHERE request_id=?""",
                (actual, _now_and_month()[0], request_id),
            )

    def cancel_not_sent(self, request_id):
        """Only safe when we KNOW the provider was never called."""
        request_id = _check_label(request_id, "リクエストID")
        with self._transaction() as db:
            row = self._get(db, request_id)
            if row is None or row[7] != "reserved":
                raise ReservationConflict("送信済み・課金不明の予約は自動解除できません。")
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
            monthly = self._used(db, "month_key=?", (month,))
            groups = db.execute(
                """SELECT state, count(*) FROM api_budget_requests
                   WHERE project_id=? GROUP BY state""", (project_id,),
            ).fetchall()
        return {
            "project_limit_jpy": _units_to_yen(self.project_limit),
            "project_used_jpy": _units_to_yen(work),
            "project_remaining_jpy": _units_to_yen(max(0, self.project_limit - work)),
            "monthly_limit_jpy": _units_to_yen(self.monthly_limit),
            "monthly_used_jpy": _units_to_yen(monthly),
            "monthly_remaining_jpy": _units_to_yen(max(0, self.monthly_limit - monthly)),
            "requests_by_status": dict(groups),
            "month": month,
        }
