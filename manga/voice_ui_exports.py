"""音声保存の復旧とタイムライン/SRTの明示出力。大きな音声は選択時だけ読む。"""
from manga.ui_runtime import ui as st
from manga.storage import StorageError
from manga.voice_timeline import save_timeline,timeline_is_current
from manga.voice_ui import act


def exports_view(service,data,prefix):
    st.subheader('字幕・FFmpeg用タイムライン')
    st.caption('発話の表示時間はWAVの実尺です。ナレーション→セリフの順で、話者名は字幕本文へ追加しません。無音コマだけ表示時間を明示指定します。')
    gap=st.number_input('発話・コマ間の余韻（ms）',min_value=0,max_value=5000,value=200,step=50,key=prefix+'_gap')
    silent=st.number_input('無音コマの表示時間（ms・手動設定）',min_value=100,max_value=60000,value=1500,step=100,key=prefix+'_silent')
    if st.button('字幕・タイムラインを新しい版で保存',type='primary'):
        act(lambda:save_timeline(service,expected_revision=data['revision'],gap_ms=gap,silent_panel_ms=silent),'字幕とタイムラインを新しい版で保存しました。')
    if not data['timelines']:st.info('音声を用意し、字幕・タイムラインを保存してください。');return
    records={r['id']:r for r in reversed(data['timelines'][-30:])}
    identifier=st.selectbox('取得するタイムライン',list(records),format_func=lambda k:records[k]['created_at']+' / '+k[:8],key=f'{prefix}_{len(data["timelines"])}_timeline')
    record=records[identifier]
    try:
        content=service.store.read_blob(record['paths']['json'],16*1024*1024)
        subtitles=service.store.read_blob(record['paths']['srt'],8*1024*1024)
        st.download_button('タイムラインJSONをダウンロード',content,file_name=f'voice_timeline_{identifier}.json',mime='application/json')
        st.download_button('字幕SRTをダウンロード',subtitles,file_name=f'voice_subtitles_{identifier}.srt',mime='text/plain')
        if st.checkbox('現在の脚本・音声・画像と照合',key=f'{prefix}_{identifier}_check'):
            if timeline_is_current(service,record):st.success('保存時と現在の制作データは一致しています。')
            else:st.warning('保存後に制作データが変わりました。最新の内容で出力し直してください。')
        with st.expander('JSON仕様・確認事項'):
            import json
            timeline=json.loads(content)
            for warning in timeline['warnings']:st.warning(warning)
            st.json(timeline,expanded=False)
        st.caption('WAVは作品フォルダのvoice/audioに保存されています。JSON/SRTと音声・画像の相対パスを維持して次の動画工程で使います。動画生成はこの段階では行いません。')
    except (ValueError,StorageError,OSError) as error:st.error(str(error))


def history_view(service,data,prefix):
    st.subheader('生成履歴・保存の復旧')
    if not data['jobs']:st.info('音声ジョブはまだありません。');return
    jobs={j['id']:j for j in reversed(data['jobs'][-30:])}
    selected=st.selectbox('確認する音声ジョブ',list(jobs),format_func=lambda k:f"{jobs[k]['state']} / {jobs[k]['utterance']['text'][:25]} / {k[:8]}",key=f'{prefix}_job')
    job=jobs[selected]
    st.write(f"状態：{job['state']} ｜ 元脚本版：{job['story_version_id']}")
    st.caption('請求照合用リクエストID：'+job['request_id'])
    if job['state']!='ready':
        st.warning('未確定・保存途中のジョブは自動再送しません。請求不明時は予算台帳の明細照合が先です。')
        if st.button('音声保存を再開（APIを再送しない）'):
            act(lambda:service.recover(selected),'保存を再開しました。追加API呼び出しはありません。')
        retry=st.checkbox('請求は照合済みで音声が使えない。次回生成の再課金を理解した',key=f'{prefix}_{selected}_close_review')
        if st.button('この未完了ジョブを履歴に残して終了',disabled=not retry):
            act(lambda:service.close_resolved_job(selected,confirmed=retry),'未完了ジョブを履歴に残して終了しました。新しい生成には改めて見積と承認が必要です。')
    else:
        asset=next(a for a in data['assets'] if a['job_id']==selected)
        if st.checkbox('この候補を試聴する',key=f'{prefix}_{selected}_history_audio'):
            try:st.audio(service.audio_bytes(asset),format='audio/wav')
            except (ValueError,OSError,StorageError) as error:st.error(str(error))
        if st.button('現在の発話へこの保存候補を採用'):
            act(lambda:service.select_asset(job['utterance']['id'],asset['id'],expected_revision=data['revision']),'現在の発話・声設定と一致する候補だけを採用しました。')
    with st.expander('履歴の詳細（APIキーは保存しません）'):st.json(job,expanded=False)
