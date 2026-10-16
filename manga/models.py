"""画面やAPIに依存しないプロジェクトのデータ定義。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from uuid import UUID, uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_id(project_id: str) -> str:
    """ファイル名には正規形式のUUIDのみを許可する。"""
    try:
        if not isinstance(project_id, str) or str(UUID(project_id)) != project_id:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise ValueError("プロジェクトIDが不正です。") from None
    return project_id


def validate_text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}を入力してください。")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{label}は{maximum}文字以内で入力してください。")
    return value


@dataclass
class WorkflowState:
    """将来、完了済み工程と成果物のパスを記録するための領域。"""

    current_step: str = "project"
    completed_steps: list = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)


@dataclass
class BudgetState:
    """第1段階のJSONとの互換用。手動連携ではこの費用項目を使用しない。"""

    limit_usd: Optional[str] = None
    spent_usd: str = "0.00"


@dataclass
class Project:
    title: str
    genre: str
    synopsis: str
    page_count: int = 8
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    schema_version: int = 1
    workflow: WorkflowState = field(default_factory=WorkflowState)
    budget: BudgetState = field(default_factory=BudgetState)

    def __post_init__(self) -> None:
        validate_id(self.id)
        self.title = validate_text(self.title, "タイトル", 100)
        self.genre = validate_text(self.genre, "ジャンル", 50)
        self.synopsis = validate_text(self.synopsis, "あらすじ", 5000)
        if type(self.page_count) is not int or not 1 <= self.page_count <= 100:
            raise ValueError("ページ数は1〜100の整数で指定してください。")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("対応していないJSONのバージョンです。")
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, str):
                raise ValueError("日時の形式が不正です。")
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("日時の形式が不正です。") from None
            if parsed.tzinfo is None:
                raise ValueError("日時にはタイムゾーンが必要です。")
        if not isinstance(self.workflow, WorkflowState) or not isinstance(self.budget, BudgetState):
            raise ValueError("進捗または予算の形式が不正です。")
        if (
            not isinstance(self.workflow.current_step, str)
            or not isinstance(self.workflow.completed_steps, list)
            or not all(isinstance(step, str) for step in self.workflow.completed_steps)
            or not isinstance(self.workflow.artifacts, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in self.workflow.artifacts.items())
        ):
            raise ValueError("進捗の形式が不正です。")
        for name, amount in (("limit_usd", self.budget.limit_usd), ("spent_usd", self.budget.spent_usd)):
            if name == "limit_usd" and amount is None:
                continue
            try:
                if not isinstance(amount, str):
                    raise ValueError
                number = Decimal(amount)
                if not number.is_finite() or number < 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                raise ValueError("費用は0以上の数値文字列で保存してください。") from None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Project":
        if not isinstance(data, dict):
            raise ValueError("JSONの形式が不正です。")
        required = {"id", "title", "genre", "synopsis", "page_count", "created_at", "updated_at", "schema_version"}
        if not required.issubset(data):
            raise ValueError("JSONに必要な項目がありません。")
        try:
            values = dict(data)
            values["workflow"] = WorkflowState(**values.get("workflow", {}))
            values["budget"] = BudgetState(**values.get("budget", {}))
            return cls(**values)
        except TypeError:
            raise ValueError("JSONの項目または型が不正です。") from None
