"""明示操作だけでGitHubと作品を受け渡す。非表示作業の通信は行わない。"""
from pathlib import Path
import os
from manga.ui_runtime import ui as st,operation
from manga.storage import StorageError,ProjectStore
from manga.models import validate_id
from manga.github_sync import SyncService,SyncConflict
from manga.git_transport import GitRemote
from manga.sync_manifest import sha256,decode_json
from manga.sync_adoption import SCOPES
from manga.sync_ui_views import import_view,candidate_view,publish_view,pdca_view


def github_token():
    token=os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:return token
    try:return st.secrets.get('GITHUB_TOKEN',None)
    except (FileNotFoundError,KeyError):return None


def service_for(directory,project_id,repository):
    cache=Path(directory)/project_id/'github_sync'/'git'/sha256(repository.encode())[:16]
    return SyncService(directory,project_id,GitRemote(repository,cache,github_token()))


def perform(action,message):
    try:
        with operation(message,invalidate_actions=True):action()
        st.session_state['_github_sync_notice']=message;st.rerun()
    except (StorageError,ValueError,OSError) as error:
        st.error(str(error))
        if isinstance(error,SyncConflict) and error.differences:
            st.dataframe([{k:v for k,v in r.items() if k!='changes'} for r in error.differences],hide_index=True,use_container_width=True)
            with st.expander('実際のJSON差分'):st.json(error.differences,expanded=False)
        st.caption('既存作品を上書きして再試行する操作はありません。最新データ・権限・容量を確認してください。')


def render_github_sync(project,directory,navigate):
    st.header('GitHub作品同期')
    st.caption('ChatGPTがGitHubに保存したJSON・画像を候補として取得します。ChatGPTの呼び出し・自動送信・API従量課金はありません。')
    notice=st.empty()
    if '_github_sync_notice' in st.session_state:notice.success(st.session_state.pop('_github_sync_notice'))
    repository=st.text_input('GitHubリポジトリ',value='shomasato-gitm/manga',key='github_sync_repository')
    work_id=st.text_input('同期する作品ID（必須）',value=project.id if project else '2d83c11f-04ee-4134-a66d-dace81c6f698',key=f'github_sync_work_{project.id if project else "new"}')
    branch=st.text_input('取得するブランチ',value='main',key='github_sync_branch')
    st.caption('認証はGITHUB_TOKEN／GH_TOKEN環境変数、Streamlit SecretsのGITHUB_TOKEN、または既存のGit認証を使います。画面・作品JSONへトークンを保存しません。')
    try:
        validate_id(work_id);service=service_for(directory,work_id,repository);data=service.store.load()
    except (ValueError,StorageError) as error:st.error(str(error));return
    prefix=f'github_sync_{work_id}_{sha256(repository.encode())[:8]}'
    try:pending=service.store.pending()
    except StorageError as error:st.error(str(error));return
    if pending:
        st.error('前回の採用保存が途中です。既存の編集を止め、再開してください。')
        if st.button('同期の採用保存を再開'):
            perform(service.store.resume,'採用保存を再開しました。')
        return
    view=st.radio('同期の作業',('GitHubから候補取得','差分確認・明示採用','GitHubへ保存','品質・PDCA・履歴'),horizontal=True,key=f'{prefix}_view')
    with st.empty().container():
        if view=='GitHubから候補取得':import_view(service,data,branch,prefix,perform)
        elif view=='差分確認・明示採用':candidate_view(service,data,prefix,perform,navigate)
        elif view=='GitHubへ保存':publish_view(service,data,branch,prefix,perform)
        else:pdca_view(service,data,prefix,perform,navigate)
