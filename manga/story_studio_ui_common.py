"""制作スタジオ共通の明示操作・依頼取得・回答取り込み。"""
import json

from manga.ui_runtime import ui as st, operation
from manga.storage import StorageError
from manga.editorial_ui_common import json_input, input_text
from manga.prompt_ui import prompt_tools
from manga.story_studio_samples import sample_studio_response

STEPS = ('参考作品・技法', '概要3案・選択', '詳細脚本', '評価・改善', '比較・確定')
NAMES = {'concepts':'概要3案JSON', 'detail':'詳細脚本JSON', 'evaluation':'制作スタジオ評価JSON', 'improvement':'制作スタジオ改善JSON'}


def move(project_id, step):
    st.session_state[f'story_studio_next_{project_id}'] = step
    st.rerun()


def act(service, action, message, step=None):
    try:
        with operation('制作JSONを検証・保存しています', invalidate_actions=True):
            action()
        st.session_state[f'story_studio_notice_{service.project_id}'] = message
        if step:
            st.session_state[f'story_studio_next_{service.project_id}'] = step
        st.rerun()
    except (ValueError, StorageError, OSError) as error:
        st.error(str(error))
        st.caption('既存データを削除して再試行しないでください。保存先・対象版を確認し、中断記録がある場合は先に再開してください。')


def request_form(service, data, request, prefix):
    kind = request['kind']
    names = {'concepts':'概要3案', 'detail':'詳細脚本', 'evaluation':'評価', 'improvement':'改善'}
    st.subheader(names[kind]+'プロンプトをコピーしてChatGPTへ手動送信')
    st.caption(f"依頼ID：{request['id']} ｜ 元の脚本版：{request['base_version_id'] or '未作成'}")
    for warning in request['warnings']:
        st.warning(warning)
    prompt_tools(request['prompt'], f"{prefix}_{request['id']}_prompt", f"{kind}_{request['id']}.txt")
    with st.expander('APIなしの動作確認サンプル（架空の内容）'):
        st.caption('本番脚本ではありません。この依頼のIDに対応したサンプルで入力・保存を確認できます。採用は利用者が判断します。')
        st.download_button('この依頼の動作確認JSONをダウンロード', json.dumps(sample_studio_response(request), ensure_ascii=False, indent=2),
                           file_name=f"sample_{kind}.json", mime='application/json', key=f"{prefix}_{request['id']}_sample")
    saved = any(r['request_id'] == request['id'] and r['status'] == '保存済み' for r in data['responses'])
    if saved:
        st.success('この依頼の回答は保存済みです。次の工程へ進むか、新しい依頼を作成してください。')
        return
    waiting = [r for r in data['responses'] if r['request_id'] == request['id'] and r['status'] == '確認待ち']
    next_step = '概要3案・選択' if kind == 'concepts' else '評価・改善'
    if waiting:
        options = {r['id']: r for r in waiting}
        selected = st.selectbox('GitHubから受け取った回答候補', list(reversed(options)), key=f'{prefix}_inbox')
        with st.expander('受信した回答JSONの内容'):
            st.json(options[selected]['payload'], expanded=False)
        if st.button('このGitHub回答を確認して保存', key=f'{prefix}_inbox_save', type='primary'):
            act(service, lambda: service.import_response(options[selected]['payload'], expected_revision=data['revision']), '確認したGitHub回答を保存しました。', next_step)
    values = json_input(NAMES[kind], f"{prefix}_{request['id']}_input")
    st.caption('回答の外側にrequest_id・project_idが必要です。story内の脚本は既存schema_version: 1のままです。')
    if st.button('回答JSONを検証して保存', type='primary', key=f"{prefix}_{request['id']}_save"):
        act(service, lambda: service.import_response(input_text(values), expected_revision=data['revision']),
            '回答を検証して保存しました。概要は比較・採用へ、脚本は新しい版の評価へ進んでください。', next_step)


def current_requests(data, history, kinds):
    latest_id = history[-1]['id'] if history else None
    return [r for r in reversed(data['requests']) if r['kind'] in kinds and r['base_version_id'] == latest_id
            and (r['kind'] != 'detail' or r['selection_id'] == data['selected_id'])]


def request_picker(service, data, history, kinds, prefix):
    requests = current_requests(data, history, kinds)
    if not requests:
        st.info('依頼作成ボタンからプロンプトを作成してください。取得だけでChatGPTへの送信は行いません。')
        return
    options = {r['id']: r for r in requests}
    selected = st.selectbox('表示する制作依頼', list(options), format_func=lambda k:f"{options[k]['kind']} / {options[k]['saved_at']} / {k[:8]}",
                            key=f"{prefix}_{requests[0]['id']}_request")
    request_form(service, data, options[selected], prefix)
