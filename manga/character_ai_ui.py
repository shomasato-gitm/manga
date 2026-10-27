"""任意のキャラクター制作画面。表示だけではAPIを呼ばず、送信は個別承認する。"""
from manga.character_ai_models import STATIC_NOTICE
from manga.character_ai_service import CharacterAIService
from manga.storage import StorageError
from manga.ui_runtime import ui as st, operation
import sqlite3

VIEWS = ('抽出・照合', '候補確認・採用', 'キャラクターバイブル', '矛盾チェック', '画像プロンプト', '履歴')


def action(function, message):
    try:
        with operation('人物設定・バイブルを処理しています', invalidate_actions=True): function()
        st.session_state['_character_ai_notice'] = message
        st.rerun()
    except (ValueError, OSError, StorageError) as error: refresh_error(str(error))
    except sqlite3.Error:refresh_error('共有予算台帳を読み込めません。初期化せず元の保存先とバックアップを確認してください。')
    except (KeyError, TypeError, AttributeError):refresh_error('入力・対象の版・保存JSONを確認してください。既存データを初期化していません。')


def refresh_error(message):
    st.session_state['_character_ai_error']=message
    st.session_state['_action_epoch']=st.session_state.get('_action_epoch',0)+1
    st.rerun()


def render_character_ai(project, directory, navigate=None):
    st.header('AIキャラクター制作・一貫性管理')
    st.info('無料モックは通信しません。Gemini 3.5は利用条件・最大費用を確認し、1送信ずつ個別承認します。画像生成APIは無効のままです。')
    st.caption('STEP 1 脚本を選ぶ → 2 抽出人物を確認 → 3 設定案を生成 → 4 修正・部分採用 → 5 バイブルを確認・保存')
    st.caption(STATIC_NOTICE)
    if project is None: st.info('先にプロジェクトを選択してください。'); return
    if '_character_ai_error' in st.session_state:st.error(st.session_state.pop('_character_ai_error'))
    if '_character_ai_notice' in st.session_state: st.success(st.session_state.pop('_character_ai_notice'))
    service = CharacterAIService(directory, project.id)
    prefix = 'character_ai_' + project.id
    try:
        history = service.stories.load_history()
        if not history:
            st.info('正式脚本を保存し、「比較・脚本確定」で人間が確定してください。候補脚本は使いません。')
            return
        data = service.store.load()
        pending = service.proposals.pending()
        if pending:
            st.warning('人物採用の保存が途中です。ほかの編集をせず再開してください。別編集があれば自動上書きせず停止します。')
            if st.button('人物採用の保存を再開'): action(service.proposals.resume, '人物採用の保存を再開しました。')
            return
        versions = {v['id']:v for v in reversed(history)}
        jump = st.session_state.pop(prefix+'_jump', None)
        if jump:
            page, panel = jump
            if any(p['page'] == page and any(c['panel'] == panel for c in p['panels']) for p in history[-1]['story']['pages']):
                st.session_state[prefix+'_version'] = history[-1]['id']
                st.session_state[prefix+'_view'] = '画像プロンプト'
                st.session_state[prefix+'_prompt_kind'] = 'panel'
                st.session_state[prefix+'_prompt_page'] = page
                st.session_state[prefix+'_'+str(page)+'_prompt_panel'] = panel
        selected = st.selectbox('対象の正式脚本版', list(versions), format_func=lambda i:versions[i]['saved_at']+' / '+i[:8], key=prefix+'_version')
        st.caption('正式版ID：' + selected)
        current = selected == history[-1]['id']
        finalized = any(f['story_version_id'] == selected and f['source_story'] == versions[selected]['story'] for f in service.proposals.editorial.load()['finalizations'])
        if not current: st.warning('過去版は閲覧用です。制作対象にするには従来の履歴で復元し、再確定してください。')
        elif not finalized:
            st.warning('この正式版は脚本未確定です。先に人間が確認・確定してください。')
            if navigate and st.button('比較・脚本確定へ'): navigate('企画・ストーリー', '比較・脚本確定')
        next_stage=st.session_state.pop(prefix+'_stage_pending',None)
        if next_stage in VIEWS:st.session_state[prefix+'_view']=next_stage
        view = st.radio('キャラクター制作の段階', VIEWS, horizontal=True, key=prefix+'_view')
        if not current and view not in ('抽出・照合', '候補確認・採用', '履歴'):
            st.info('過去版の制作情報は「履歴」で閲覧できます。現在の人物設定と混同しないため、編集・検査・新規プロンプトは最新正式版で行います。')
            return
        areas={name:st.empty() for name in VIEWS}
        with areas[view].container():
            if view == '抽出・照合':
                from manga.character_ai_ui_proposals import extraction
                extraction(service, project, selected, current and finalized, prefix,navigate)
            elif view == '候補確認・採用':
                from manga.character_ai_ui_proposals import review
                review(service, selected, current and finalized, prefix)
            elif view == 'キャラクターバイブル':
                from manga.character_ai_ui_bible import bible_editor
                bible_editor(service, selected, data, current and finalized, prefix, navigate)
            elif view == '矛盾チェック':
                from manga.character_ai_ui_checks import checks
                checks(service, data, prefix, navigate)
            elif view == '画像プロンプト':
                from manga.character_ai_ui_prompts import prompts
                prompts(service, data, current and finalized, prefix, navigate)
            else:
                from manga.character_ai_ui_prompts import history_view
                history_view(service, data, prefix)
    except (ValueError, OSError, StorageError) as error: st.error(str(error))
    except sqlite3.Error:st.error('共有予算台帳を読み込めません。初期化せず元の保存先とバックアップを確認してください。')
    except (KeyError, TypeError, AttributeError): st.error('キャラクター制作JSONの項目・対象版を確認してください。削除・初期化せずバックアップを確認してください。')
