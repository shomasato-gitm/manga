"""制作ダッシュボードの9工程と、既存画面への移動。自動生成は実行しない。"""
from manga.ui_runtime import ui as st, operation
from manga.storage import StorageError
from manga.production_dashboard import dashboard, MANUAL_DELIVERY, confirm_delivery
from manga.production_media import validate_media
from manga.video_storage import VideoStore


def render_dashboard(project, directory, navigate):
    try:
        result = dashboard(project, directory)
    except (StorageError, OSError, ValueError) as error:
        st.error(str(error))
        return
    prefix = 'production_'+project.id
    rows = result['rows']
    def open_recovery(item):
        if item['section'] == 'AI画像生成・採用':
            st.session_state['image_ai_'+project.id+'_view'] = '履歴・復旧'
        elif item['section'] == '音声制作・字幕':
            st.session_state['voice_view_'+project.id] = '履歴・復旧'
        elif item['section'] == '動画制作・MP4出力':
            from manga.voice_storage import VoiceStore
            timelines = VoiceStore(directory,project.id).load()['timelines']
            if timelines:
                identifier = timelines[-1]['id']
                st.session_state['video_timeline_'+project.id] = identifier
                st.session_state['video_'+project.id+'_'+identifier+'_view'] = '出力履歴'
        navigate(item['menu'],item['section'])
    st.caption('保存済みデータから9工程を判定します。画像API送信は無効・音声は無料モック・動画は既存FFmpegを利用します。')
    st.subheader('次にやること')
    if result['recoveries']:
        recovery = result['recoveries'][0]
        st.info('採用保存の再開・実行状態の確認：'+recovery['label']+'を先に確認・再開してください。保存済み履歴は保持しています。')
        if st.button('次の工程へ進む',type='primary'):
            open_recovery(recovery)
        for index, item in enumerate(result['recoveries']):
            if st.button(item['label']+'の画面へ',key=prefix+'_recovery_'+str(index)):
                open_recovery(item)
    elif result['next_step']:
        step = result['next_step']
        st.info(step['label']+'：'+step['state']+'。'+step['action'])
        if st.button('次の工程へ進む',type='primary'):
            navigate(step['menu'],step['section'])
    else:
        st.success('全9工程の成果物と最終確認がそろっています。')
    if st.button('制作を再開する',type='primary'):
        if result['recoveries']: open_recovery(result['recoveries'][0])
        elif result['next_step']: navigate(result['next_step']['menu'],result['next_step']['section'])
        else: navigate('完成・書き出し','品質チェック・修正管理')
    st.progress(sum(r['state'] == '完了' for r in rows)/len(rows), text=f"完了工程 {sum(r['state'] == '完了' for r in rows)}/{len(rows)}")
    unfinished_count=sum(not p['selected'] or p['stale'] or not p['prompt_ready'] for p in result['panels'])
    columns=st.columns(3)
    columns[0].metric('制作ページ',len({p['page'] for p in result['panels']}))
    columns[1].metric('未完成のコマ',unfinished_count)
    columns[2].metric('要再確認の工程',len(result['updated']))
    from manga.ux import progress_card
    st.subheader('工程ごとの進み具合')
    for offset in range(0,len(rows),3):
        for column,row in zip(st.columns(3),rows[offset:offset+3]):
            with column:
                with st.container(border=True):
                    progress_card(row['label'],row['done'],row['total'],row['state'])
                    if row['missing']: st.caption(row['missing'][0])
                    if st.button('この工程を開く',key=prefix+'_card_'+row['key']): navigate(row['menu'],row['section'])
    with st.expander('工程ごとの詳しい条件'):
        st.dataframe([{'工程':r['label'],'状態':r['state'],'完了数 / 全体数':f"{r['done']} / {r['total']}",'次に必要な操作':r['action'],'不足データ・理由':' / '.join(r['missing'])} for r in rows],hide_index=True,use_container_width=True)
    for row in rows:
        with st.expander(row['label']+' / '+row['state']+f" / {row['done']}/{row['total']}"):
            st.write(row['action'])
            for missing in row['missing'][:30]:
                st.write('・'+missing)
            if len(row['missing']) > 30: st.caption('残り'+str(len(row['missing'])-30)+'件は対象の制作画面で確認できます。')
            if st.button(row['label']+'の制作画面へ',key=prefix+'_stage_'+row['key']):
                navigate(row['menu'],row['section'])
            if row['key'] == 'pages' and st.button('漫画ページ編集を開く',key=prefix+'_edit_pages'):
                navigate('漫画ページ編集')
    if result['updated']:
        st.warning('更新・再確認が必要な成果物：'+' / '.join(result['updated']))
    for error in result['output_errors']:
        st.warning(error)
    st.subheader('未完成のコマ')
    unfinished = [p for p in result['panels'] if not p['selected'] or p['stale'] or not p['prompt_ready']]
    if unfinished:
        st.dataframe([{'ページ':p['page'],'コマ':p['panel'],'状態':p['status'],'要確認':p['stale'],'プロンプト準備済み':p['prompt_ready']} for p in unfinished],hide_index=True,use_container_width=True)
        selected = st.selectbox('ダッシュボードから制作する未完成コマ',[p['key'] for p in unfinished],key=prefix+'_panel')
        if st.button('この未完成コマを手動画像制作で開く'):
            st.session_state['manual_'+project.id+'_jump'] = tuple(int(n) for n in selected.split(':'))
            navigate('イラスト制作','手動画像制作')
    else:
        st.caption('未完成コマはありません。')
    st.subheader('音声・字幕・動画の実ファイル検証')
    st.caption('ハッシュ、最新脚本との一致、SRT、WAV実尺、3形式MP4を明示操作時に検証します。素材変更後は検証結果が要確認になります。')
    if st.button('保存済みの音声・字幕・動画を検証',disabled=bool(result['recoveries'])):
        try:
            with operation('音声・字幕・動画を検証しています',invalidate_actions=True):
                validate_media(directory,project.id)
            st.rerun()
        except (StorageError,OSError,ValueError) as error:
            st.error(str(error))
    if result['proof']:
        for message in result['proof']['errors']:
            st.warning(message)
        for message in result['proof']['warnings']:
            st.caption(message)
    st.subheader('完成前の品質確認')
    st.dataframe([{'確認項目':label,'状態':'確認済み' if ok else '未完了・未確認'} for label,ok,_ in result['checks']],hide_index=True,use_container_width=True)
    if st.button('品質チェック・修正管理を開く'):
        navigate('完成・書き出し','品質チェック・修正管理')
    with st.expander('完成品・素材権利の最終確認を保存'):
        st.caption('PNG・PDFと3形式のMP4を実際に開いてから確認してください。無料モックのトーンやテスト画像がある作品は完成扱いにできません。')
        checks = {k:st.checkbox(label,key=prefix+'_'+result['token']+'_'+k) for k,label in MANUAL_DELIVERY.items()}
        note = st.text_area('最終確認メモ・素材権利の根拠',max_chars=4000,key=prefix+'_'+result['token']+'_note')
        ready = all(ok for _,ok,_ in result['checks'][:-1]) and all(r['state'] == '完了' for r in result['rows'][:-1]) and not result['recoveries']
        if st.button('完成品の最終確認を保存',disabled=not ready or not all(checks.values()) or not note.strip()):
            try:
                confirm_delivery(project,directory,checks,note,expected_revision=VideoStore(directory,project.id).load()['revision'],expected_token=result['token'])
                st.rerun()
            except (StorageError,OSError,ValueError) as error:
                st.error(str(error))
