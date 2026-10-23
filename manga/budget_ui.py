"""Read-only budget status screen; no API credentials or network calls."""
from pathlib import Path

from manga.budget import BudgetLedger
from manga.ui_runtime import ui as st


def render_budget(project, projects_directory):
    if project is None:
        st.info("まず作品を選択してください。")
        return

    db_path = Path(projects_directory).parent / "runtime" / "budget.sqlite3"
    ledger = BudgetLedger(db_path, project_limit_jpy="500", monthly_limit_jpy="5000")
    info = ledger.summary(project.id)

    st.subheader("動画制作のAPI予算")
    st.caption("課金APIはまだ接続されていません。残額は予算台帳に記録された予約・精算額です。")
    st.write(f"作品: {project.title}")
    st.write(
        f"作品上限: {info['project_limit_jpy']:.2f}円 / "
        f"予約・利用済み: {info['project_used_jpy']:.2f}円 / "
        f"残額: {info['project_remaining_jpy']:.2f}円"
    )
    st.write(
        f"全作品月間 ({info['month']}): "
        f"{info['monthly_used_jpy']:.2f} / {info['monthly_limit_jpy']:.2f}円"
    )
    if info["requests_by_status"].get("uncertain", 0):
        st.warning("請求状態が不明なAPI実行があります。利用明細を照合してから手動で解決してください。")
    st.caption(
        "予約金額は課金額とは限りません。タイムアウト時の予約は維持されます。"
        "アプリ内の上限では提供元の最終請求額を厳密に保証できません。"
    )
