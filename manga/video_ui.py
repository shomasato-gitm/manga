"""任意の動画画面。選択した計画・プレビュー・MP4だけを読み込む。"""
import copy

from manga.ui_runtime import ui as st,operation
from manga.storage import StorageError
from manga.video_service import VideoService
from manga.video_prereqs import video_ready
from manga.video_models import FORMATS,compile_plan
from manga.voice_models import digest


def action(function,message):
    try:
        with operation('動画データを処理しています',invalidate_actions=True):function()
        st.session_state['_video_notice']=message;st.rerun()
    except (ValueError,OSError,StorageError) as error:st.error(str(error))
    except (KeyError,TypeError,AttributeError):st.error('動画設定・保存記録の必須項目を確認してください。既存素材は変更していません。')


def render_video(project,directory):
    st.header('動画制作・MP4出力')
    st.caption('音声タイムラインを選択 → 3形式の計画を保存 → 素材検査 → 無料レンダリング → 再生・ダウンロード')
    st.info('FFmpegと既存素材だけを使用します。有料APIは呼びません。SNSへの投稿は行いません。')
    if not video_ready():
        st.warning('ffmpegとffprobeが見つかりません。動画機能だけ停止します。Macの導入手順はdocs/VIDEO_RENDERING.mdを参照してください。');return
    if project is None:st.info('先に作品を選択してください。');return
    if '_video_notice' in st.session_state:st.success(st.session_state.pop('_video_notice'))
    service=VideoService(directory,project.id)
    try:
        records=service.voice.store.load()['timelines'][-30:];data=service.store.load()
        if not records:st.info('音声制作・字幕でWAVとタイムラインを保存してください。');return
        identifier=st.selectbox('動画に使う音声タイムライン',[r['id'] for r in reversed(records)],key=f'video_timeline_{project.id}')
        timeline=service.timeline(identifier)
        base=f'video_{project.id}_{identifier}';draft_key=base+'_draft'
        plans=st.session_state.setdefault(draft_key,service.plans(timeline))
        epoch=st.session_state.get(base+'_epoch',0);prefix=f'{base}_{epoch}'
        from manga.ux import wizard
        wizard('動画の手順',('編集計画','生成・進行状況','出力履歴'),st.session_state.get(base+'_view','編集計画'),lambda value:st.session_state.update({base+'_view':value}))
        view=st.radio('動画制作の作業',('編集計画','生成・進行状況','出力履歴'),horizontal=True,key=base+'_view')
        if view=='編集計画':
            from manga.video_ui_editor import editor
            editor(service,data,timeline,plans,prefix,selection_key=base+'_kind')
            if st.button('3形式の編集計画を保存',type='primary'):
                action(lambda:service.save_plans(identifier,plans,expected_revision=data['revision']),'編集計画を履歴付きで保存しました。次は素材検査です。')
            if st.button('未保存の編集をキャンセル'):
                st.session_state[draft_key]=service.plans(timeline);st.session_state[base+'_epoch']=epoch+1;st.rerun()
        elif view=='生成・進行状況':generation(service,data,timeline,plans,prefix)
        else:
            from manga.video_ui_history import history
            history(service,data,prefix)
    except (ValueError,OSError,StorageError) as error:st.error(str(error))
    except (KeyError,TypeError,AttributeError):st.error('動画JSON・音声タイムラインの項目を確認してください。既存データは上書きしていません。')


def generation(service,data,timeline,plans,prefix):
    saved=data['plans'].get(timeline['id'])
    if saved!=plans:st.warning('先に編集計画を保存してください。未保存の設定では生成しません。');return
    kinds=st.multiselect('出力する動画',list(FORMATS),default=list(FORMATS),key=prefix+'_kinds')
    if not kinds:return
    token=digest({k:plans[k] for k in kinds});key=prefix+'_'+token+'_inspection'
    if st.button('素材・版・字幕を検査',type='primary'):
        try:
            with operation('画像・WAV・字幕・版を検証しています'):
                source=service.inspect(timeline['id'],{k:plans[k] for k in kinds})
            st.session_state[key]=dict(warnings=source['warnings'])
        except (ValueError,OSError,StorageError) as error:st.error(str(error))
    inspection=st.session_state.get(key)
    if inspection:
        for warning in inspection['warnings']:st.warning(warning)
        st.success('素材検査が完了しました。生成開始時と完成確定時にも再照合します。')
    st.caption('保存先：'+str(service.store.root/'video'/'exports'))
    for kind in kinds:
        schedule=compile_plan(plans[kind],timeline);st.write(f'{kind}：{FORMATS[kind][0]}×{FORMATS[kind][1]} / {schedule["duration_ms"]/1000:.2f}秒 / H.264・AAC')
    accepted=st.checkbox('実尺・素材と未確認事項を確認した。生成後も公開前の目視・試聴が必要',key=prefix+'_'+token+'_accept')
    force=st.checkbox('履歴を残して新しいMP4を再出力する（追加API課金なし）',key=prefix+'_force')
    if st.button('選択形式の動画生成を開始',type='primary',disabled=not accepted or not inspection):
        def start():
            job=service.create_job(timeline['id'],kinds,expected_revision=data['revision'],acknowledged=accepted,force=force)
            if job['state']=='queued':service.start(job['id'])
        action(start,'動画処理を受け付けました。状態を更新して進行状況を確認してください。')
    st.subheader('動画ジョブの進行状況')
    st.caption('バックグラウンドで処理します。アプリを終了すると中断されることがあります。更新ボタンで確認できます。')
    if st.button('動画の状態を更新'):st.rerun()
    for job in reversed(data['jobs'][-5:]):
        st.write(f"{job['state']} / {job['id'][:8]} / {job['created_at']}")
        progress=job['progress']
        if progress['total']:st.progress(progress['done']/progress['total'],text=f"{progress['kind']} {progress['done']}/{progress['total']}シーン")
        if job['error']:st.error(job['error'])
        if job['state'] in ('running','queued') and st.button('この動画処理を中断',key=prefix+job['id']+'_cancel'):
            action(lambda:service.cancel(job['id']),'中断を要求しました。元画像・音声・履歴を保持します。')
