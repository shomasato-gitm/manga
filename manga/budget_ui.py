"""Read-only budget status screen; no API credentials or network calls."""
import sqlite3

from manga.budget.config import open_budget
from manga.ui_runtime import ui as st


def render_budget(project, projects_directory):
    if project is None:
        st.info("まず作品を選択してください。")
        return

    try:
        ledger = open_budget(projects_directory)
        info = ledger.summary(project.id)
    except (sqlite3.Error, OSError, ValueError):
        st.error('予算台帳を安全に読み込めません。API処理を停止してください。台帳を削除・再作成せず、保存先・権限・予算設定・バックアップを確認してください。')
        return

    st.subheader("動画制作のAPI予算")
    st.caption("課金APIはまだ接続されていません。残額は予算台帳に記録された予約・精算額です。")
    st.caption('台帳の保存先：'+str(ledger.path))
    st.write(f"作品: {project.title}")
    st.write(
        f"作品上限: {info['project_limit_jpy']:.2f}円 / "
        f"予約・利用済み: {_money(info['project_used_jpy'])}円 / "
        f"残額: {_money(info['project_remaining_jpy'])}円"
    )
    st.write(
        f"全作品月間 ({info['month']}): "
        f"{_money(info['monthly_used_jpy'])} / {info['monthly_limit_jpy']:.2f}円 / "
        f"月間残額: {_money(info['monthly_remaining_jpy'])}円"
    )
    pending = sum(info['requests_by_status'].get(k,0) for k in ('reserved','uncertain'))
    st.write(f"請求未確定: この作品 {pending}件 / 全作品 {info['monthly_pending_count']}件（過去月からの持ち越しを含む）")
    if info['blocked']:
        st.warning(f"新しいAPI実行を停止中です。全作品の請求額不明 {info['uncertain_count']}件、未確認の見積超過 {info['unreviewed_overrun_count']}件。明細と料金見積を手動確認してください。")
    st.caption(
        "予約金額は課金額とは限りません。タイムアウト時の予約は維持されます。"
        "アプリ内の上限では提供元の最終請求額を厳密に保証できません。"
    )


def _money(value):
    text = f'{value:.6f}'
    while text.endswith('0') and len(text.split('.')[1])>2:
        text = text[:-1]
    return text
