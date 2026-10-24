"""One shared persistent ledger location for the UI and future paid adapters."""
import os
from pathlib import Path

from .ledger import BudgetLedger


def budget_path(projects_directory):
    override = os.environ.get('MANGA_BUDGET_DB')
    if override is not None:
        if not override.strip() or not Path(override).expanduser().is_absolute():
            raise ValueError('MANGA_BUDGET_DBは永続保存先の絶対パスで指定してください。')
        path = Path(override).expanduser()
    else:
        path = Path(projects_directory).parent/'runtime'/'budget.sqlite3'
    return path


def open_budget(projects_directory):
    return BudgetLedger(budget_path(projects_directory),project_limit_jpy='500',monthly_limit_jpy='5000')
