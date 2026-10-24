"""6工程内の任意画面。選んだ工程だけを描画し、実APIへは接続しない。"""
from manga.image_ai_models import NOTICE
from manga.image_ai_service import ImageAIService
from manga.storage import StorageError
from manga.ui_runtime import ui as st, operation

VIEWS=('生成計画','モデル・見積','生成・手動取り込み','候補比較・採用','履歴・復旧')


def action(function,message):
    try:
        with operation('画像候補・保存状態を処理しています',invalidate_actions=True): function()
        st.session_state['_image_ai_notice']=message; st.rerun()
    except (ValueError,OSError,StorageError) as error: st.error(str(error))
    except (KeyError,TypeError,AttributeError,StopIteration): st.error('対象の版・依頼・入力JSONを確認してください。既存画像を初期化していません。')


def render_image_ai(project,directory,navigate=None):
    st.header('AI画像生成・採用')
    st.info('現在は無料モック／手動画像専用です。有料APIは未対応・送信不可です。')
    st.caption(NOTICE)
    if project is None: st.info('先にプロジェクトを選択してください。'); return
    if '_image_ai_notice' in st.session_state: st.success(st.session_state.pop('_image_ai_notice'))
    service=ImageAIService(directory,project.id); prefix='image_ai_'+project.id
    try:
        data=service.store.load(); pending=service.store.pending()
        if pending:
            st.warning('画像採用の保存が途中です。他の編集をせず、同じ登録の保存を再開してください。')
            confirmed=st.checkbox('同じ候補・画像IDで保存だけを再開することを確認した',key=prefix+'_resume_adoption')
            if st.button('画像採用の保存を再開',disabled=not confirmed): action(lambda:service.recover_adoption(confirmed=confirmed),'画像採用の保存を再開しました。二重登録していません。')
            return
        if not service.store.stories.load_history(): st.info('正式脚本を保存・確定し、人物バイブルを確認してから始めてください。'); return
        view=st.radio('画像制作の段階',VIEWS,horizontal=True,key=prefix+'_view')
        if view=='生成計画':
            from manga.image_ai_ui_plan import overview,plan_editor
            overview(service,prefix,navigate); plan_editor(service,prefix,navigate)
        elif view=='モデル・見積':
            from manga.image_ai_ui_plan import estimate
            estimate(service,prefix)
        elif view=='生成・手動取り込み':
            from manga.image_ai_ui_jobs import generation
            generation(service,data,prefix)
        elif view=='候補比較・採用':
            from manga.image_ai_ui_candidates import candidates
            candidates(service,data,prefix,navigate)
        else:
            from manga.image_ai_ui_jobs import history
            history(service,data,prefix)
    except (ValueError,OSError,StorageError) as error: st.error(str(error))
    except (KeyError,TypeError,AttributeError,StopIteration): st.error('画像制作の保存JSON・対象版を確認してください。既存データを変更していません。')
