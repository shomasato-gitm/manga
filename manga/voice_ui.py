"""選択した音声工程だけ描画する。既定はAPIなしの確認用モック。"""
import sqlite3
from manga.ui_runtime import ui as st, operation
from manga.storage import StorageError
from manga.voice_service import VoiceService


def act(action,message):
    try:
        with operation('音声制作データを処理しています',invalidate_actions=True):action()
        st.session_state['_voice_notice']=message
        st.rerun()
    except (ValueError,StorageError,OSError) as error:
        st.error(str(error));st.caption('既存音声・請求予約を削除せず、対象版と履歴・復旧を確認してください。')
    except sqlite3.Error:
        st.error('予算台帳を安全に読み込めません。台帳を削除・初期化せず、保存先とバックアップを確認してください。')
    except (KeyError,TypeError,AttributeError):
        st.error('設定JSONの必須項目・型を確認してください。既存設定は上書きしていません。')


def render_voice(project,directory,navigate=None):
    st.header('音声制作・字幕')
    st.caption('話者・声を設定 → 発話を確認 → 音声を保存・試聴 → 字幕とタイムラインを出力')
    if project is None:
        st.info('先に作品を選択してください。');return
    if '_voice_notice' in st.session_state:st.success(st.session_state.pop('_voice_notice'))
    service=VoiceService(directory,project.id)
    try:
        data=service.store.load();plan=service.plan(data)
    except (ValueError,StorageError,OSError) as error:
        st.info(str(error));return
    if not plan['confirmed']:
        st.warning('現在の脚本は未確定です。確認用モックは使えますが、有料音声は脚本確定後に利用します。')
        if navigate and st.button('ストーリーの確定画面へ'):
            navigate('企画・ストーリー','比較・脚本確定')
    st.caption(f"脚本版：{plan['version']['id']} ｜ 発話：{len(plan['rows'])}件 ｜ 保存音声：{len(data['assets'])}件")
    if not service.paid_enabled():st.info('有料TTSは無効です。確認用トーンを生成でき、API課金は発生しません。')
    from manga.ux import wizard,details
    steps=('準備・話者設定','音声生成・試聴','字幕・タイムライン','履歴・復旧')
    view_key=f'voice_view_{project.id}'
    def move(value): st.session_state[view_key]=value
    wizard('音声の手順',steps,st.session_state.get(view_key,steps[0]),move)
    view=st.radio('音声制作の作業',('準備・話者設定','音声生成・試聴','字幕・タイムライン','履歴・復旧'),horizontal=True,key=f'voice_view_{project.id}')
    prefix=f'voice_{project.id}'
    with st.empty().container():
        if view=='準備・話者設定':
            from manga.voice_ui_settings import settings_view
            settings_view(service,data,plan,prefix)
        elif view=='音声生成・試聴':
            from manga.voice_ui_generate import generation_view
            generation_view(service,data,plan,prefix)
        else:
            from manga.voice_ui_exports import exports_view,history_view
            if view=='字幕・タイムライン':exports_view(service,data,prefix)
            else:history_view(service,data,prefix)
