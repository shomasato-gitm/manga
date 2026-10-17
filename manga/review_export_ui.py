"""書き出し直前の案内。主観的な指摘を出力禁止の条件にしない。"""
from manga.ui_runtime import ui as st
from manga.ui_data import read_store
from manga.review_context import load_context
from manga.review_storage import ReviewStore
from manga.review_status import export_warnings
from manga.storage import StorageError


def export_notice(directory, project_id, pages, *, sns_width=None):
    try:
        context = load_context(directory, project_id)
        data = read_store(ReviewStore(directory, project_id))
        notes = export_warnings(context, data, pages, sns_width=sns_width)
        for note in notes: st.warning(note)
        if notes: st.caption('警告を確認したうえで「出力ファイルを生成」を押せます。主観的なAI指摘や未確認項目だけでは禁止しません。描画不能な文字・フォントエラーは出力時に停止します。')
        else: st.success('選択したページは現在の保存データについて最終確認済みです。')
    except StorageError as error:
        st.warning(f'品質確認状態を取得できません：{error}。品質データのバックアップを確認してください。書き出し時の描画検証は実行します。')
