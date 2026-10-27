"""6工程を維持した任意のAI企画・ストーリースタジオ。既定はモック。"""
import sqlite3
from manga.ui_runtime import ui as st,operation
from manga.storage import StorageError
from manga.story_ai_service import StoryAIService


def action(function,message):
    try:
        with operation('企画・脚本データを処理しています',invalidate_actions=True):function()
        st.session_state['_story_ai_notice']=message;st.rerun()
    except (ValueError,OSError,StorageError) as error:st.error(str(error))
    except sqlite3.Error:st.error('予算台帳を安全に読み込めません。初期化せず、保存先とバックアップを確認してください。')
    except (KeyError,TypeError,AttributeError):st.error('設定・JSON・対象の版を確認してください。既存の脚本は上書きしていません。')


def render_story_ai(project,directory,navigate=None):
    st.header('AI企画・ストーリースタジオ')
    st.caption('STEP 1 条件入力 → 2 AI選択 → 3 料金・送信内容を確認 → 4 1回生成 → 5 候補比較 → 6 採用・改善 → 7 正式確定')
    if project is None:st.info('先にプロジェクトを選択してください。');return
    if '_story_ai_notice' in st.session_state:st.success(st.session_state.pop('_story_ai_notice'))
    service=StoryAIService(directory,project.id);prefix='story_ai_'+project.id
    try:
        data=service.store.load()
        pending=service.store.safe('story_ai/adoption_pending.json')
        if pending.is_file():
            import json
            if json.loads(pending.read_text(encoding='utf-8'))['state']=='pending':
                st.warning('正式採用の保存が途中です。ほかの保存をせず、採用保存を再開してください。')
                if st.button('正式採用の保存を再開（APIなし）'):action(service.recover_adoption,'採用保存を再開しました。')
        if not service.paid_enabled():st.info('有料APIは無効です。モックで全工程を無料確認できます。モックの脚本・点数は実モデルの品質を表しません。')
        modes={'manual':'手動モード（ChatGPTなど）','mock':'無料モック','verified-free':'確認済み無料API','paid':'有料API'}
        mode=st.selectbox('企画・脚本の生成方法',list(modes),format_func=modes.get,key=prefix+'_provider')
        provider=mode
        if mode in ('verified-free','paid'):
            provider=st.selectbox('利用するテキストAI',('gemini-text','openai-responses'),format_func=lambda p:'Gemini' if p=='gemini-text' else 'OpenAI Responses',key=prefix+'_api_provider')
            st.session_state[prefix+'_mode']=mode
            import os
            st.caption('APIキー：'+('設定あり' if os.environ.get('GEMINI_API_KEY' if provider=='gemini-text' else 'OPENAI_API_KEY') else '未設定')+'（値は表示しません）')
            st.info('接続実装済み・実通信未検証。公式資料の確認だけでは、このアカウントの利用権限は確認できません。')
        if mode=='manual':
            st.info('ChatGPTで生成したJSONを従来の取り込み画面で確認できます。ここから自動送信は行いません。')
            if navigate and st.button('ChatGPTのJSON取り込み画面へ'):navigate('企画・ストーリー','生成・取り込み')
        view=st.radio('AIストーリーの制作段階',('制作条件','企画案の生成・比較','脚本制作・自動改善','比較・採用・履歴'),horizontal=True,key=prefix+'_view')
        if view=='制作条件':
            from manga.story_ai_ui_settings import configuration
            configuration(service,data,project,prefix)
        elif view=='企画案の生成・比較':
            from manga.story_ai_ui_concepts import concepts
            concepts(service,data,provider,prefix)
        elif view=='脚本制作・自動改善':
            from manga.story_ai_ui_jobs import workflow
            workflow(service,data,provider,prefix)
        else:
            from manga.story_ai_ui_compare import comparison
            comparison(service,data,prefix,navigate)
        if navigate and st.button('STEP 7：最新脚本を確認・正式確定する'):navigate('企画・ストーリー','比較・脚本確定')
        if navigate and any(e['kind']=='gemini_connection_diagnostic' for e in data['events']) and st.button('請求未確定の明細・手動照合へ'):
            navigate('完成・書き出し','API予算管理')
    except (ValueError,OSError,StorageError) as error:st.error(str(error))
    except sqlite3.Error:st.error('予算台帳を安全に読み込めません。初期化せず、保存先とバックアップを確認してください。')
    except (KeyError,TypeError,AttributeError):st.error('AI制作JSONの項目・対象の版を確認してください。既存のデータは変更していません。')
