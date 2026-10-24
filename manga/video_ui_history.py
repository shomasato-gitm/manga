"""選択した出力だけを再生・取得する。失敗ファイルは完成品に並べない。"""
from manga.ui_runtime import ui as st
from manga.video_ui import action


def history(service,data,prefix):
    st.subheader('完成ファイル・再出力・保存再開')
    st.warning('生成済みは公開許可を意味しません。確認用トーン・未確認素材・字幕・人物の見切れを、必ずMP4で確認してください。')
    records={e['id']:e for e in reversed(data['exports'][-30:])}
    if records:
        selected=st.selectbox('確認するMP4',list(records),format_func=lambda i,items=records:items[i]['kind']+' / '+items[i]['created_at'],key=prefix+'_export')
        record=records[selected];st.write(record['metadata']);st.caption('元タイムライン：'+record['timeline_id'])
        for warning in record['warnings']:st.warning(warning)
        if st.checkbox('選択したMP4を読み込む（1ファイルのみ）',key=prefix+'_'+selected+'_load'):
            try:
                content=service.export_bytes(record);st.video(content)
                st.download_button('MP4をダウンロード',content,file_name=record['kind']+'.mp4',mime='video/mp4')
            except (ValueError,OSError) as error:st.error(str(error))
    else:st.info('完成確定した動画はまだありません。')
    jobs={j['id']:j for j in reversed(data['jobs'][-30:])}
    if jobs:
        identifier=st.selectbox('保存再開・状態確認するジョブ',list(jobs),format_func=lambda i:jobs[i]['state']+' / '+i[:8],key=prefix+'_recover')
        if st.button('保存再開／中断状態を確認（APIなし）'):
            action(lambda:service.recover(identifier),'動画の保存状態を確認しました。')
    records={r['id']:r for r in reversed(data['plan_history'][-30:])}
    if records:
        identifier=st.selectbox('復元する編集計画の履歴',list(records),format_func=lambda i,items=records:items[i]['saved_at']+' / '+i[:8],key=prefix+'_plan_history')
        if st.button('過去の編集計画を新しい履歴として復元'):
            def restore():
                service.restore_plans(identifier,expected_revision=data['revision'])
                # Clear only video drafts so the restored plan is shown on return.
                for key in list(st.session_state):
                    if key.startswith('video_'+service.project_id+'_') and key.endswith('_draft'):del st.session_state[key]
            action(restore,'過去の計画を復元しました。元の履歴は保持しています。')
