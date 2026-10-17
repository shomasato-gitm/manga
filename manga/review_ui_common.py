"""品質画面の保存通知と対象画面への往復。"""
from manga.ui_runtime import ui as st, operation
from manga.storage import StorageError
from manga.review_models import CORRECTION_TARGETS


def perform(action, message):
    try:
        with operation(message, invalidate_actions=True): action()
        st.session_state['_review_notice'] = message
        st.rerun()
    except (StorageError, ValueError, OSError) as error:
        st.error(f'{error} 入力は自動適用していません。最新状態を確認して再試行してください。')


def issue_label(issue):
    where = f"{issue['page']}ページ" if issue['page'] else '作品全体'
    if issue['panel']: where += f"・{issue['panel']}コマ"
    return f"{where}：{issue['message']}"


def issue_rows(issues):
    return [{'対象': f"{i['page'] or '全体'}ページ" + (f"・{i['panel']}コマ" if i['panel'] else ''),
             '重要度': i['severity'], '種類': i['category'], '内容': i['message'], '修正対象': CORRECTION_TARGETS[i['target']]} for i in issues]


def open_issue(project_id, issue, navigate, *, task_id=None):
    st.session_state['_review_return'] = dict(project_id=project_id, page=issue['page'], task_id=task_id)
    if issue['target'] in ('image', 'direction') and issue['page'] and issue['panel']:
        from manga.panel_workspace_ui import open_panel
        open_panel(project_id, issue['page'], issue['panel'], navigate)
    elif issue['target'] in ('bubble', 'layout') and issue['page']:
        st.session_state[f'composition_{project_id}_page_jump'] = issue['page']
        navigate('漫画ページ編集')
    elif issue['target'] == 'story':
        navigate('企画・ストーリー', 'ページ編集')
    elif issue['target'] == 'settings':
        navigate('完成・書き出し', 'PNG・PDF書き出し')
    else:
        navigate('絵コンテ・作画指示', 'コマ一覧・制作へ')


def return_link(project, menu, section, navigate):
    origin = st.session_state.get('_review_return')
    if not project or not origin or origin['project_id'] != project.id or section == '品質チェック・修正管理': return
    st.info('品質チェックから修正中です。保存後に戻り、再検査・再確認してください。')
    if st.button('品質チェックへ戻る', key='review_return'):
        st.session_state[f'review_{project.id}_jump'] = origin
        navigate('完成・書き出し', '品質チェック・修正管理')
