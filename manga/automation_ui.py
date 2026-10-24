"""無料処理のプレビュー・承認・バックグラウンド実行。"""
from manga.ui_runtime import ui as st
from manga.storage import StorageError
from manga.automation import Automation, LABELS


def render_automation(project,directory,navigate):
    st.subheader('制作工程の自動進行')
    st.caption('採用・同定・脚本確定・目視・素材権利・公開判断は自動化しません。有料API・外部送信・GitHub同期・SNS投稿は実行しません。')
    engine=Automation(directory,project.id)
    try:
        plan=engine.plan()
        active=False
        try:
            if (engine.root/'.automation-run.lock').exists():
                with engine.lock(): pass
        except StorageError: active=True
        st.write('状態：'+('処理中' if active else plan['status']))
        st.caption('保存先：'+plan['destination'])
        st.write(f"現在の実行可能な処理：{len(plan['actions'])}件（一括上限32処理）")
        st.dataframe([dict(順番=i+1,処理=a['label'],対象=str(a['args']),依存条件=' / '.join(a['dependencies'])) for i,a in enumerate(plan['actions'])],hide_index=True,use_container_width=True)
        st.caption('一括実行は、この対象から始めて依存順に最大32処理まで進めます。ページ出力→全体出力、無料音声→字幕→承認済み動画計画→3形式MP4→検証の順です。条件不足・人間の確認待ちで停止します。')
        for message in plan['blocked']: st.info(message)
        approved=st.checkbox('対象・件数・保存先・無料処理の順序と上限を確認し、実行を承認する',key='auto_approve_'+project.id+'_'+plan['token'])
        disabled=active or not approved or not plan['actions']
        if st.button('次の自動処理を実行',disabled=disabled):
            engine.start(plan['token']); st.rerun()
        if st.button('実行可能な無料処理を進める',disabled=disabled):
            engine.start(plan['token'],batch=True); st.rerun()
        if st.button('自動処理の状態を更新'): st.rerun()
        jobs=engine.history()
        if jobs:
            st.dataframe([dict(処理=LABELS[j['action']['kind']],状態=j['state'],開始=j['started_at'],確認内容=j.get('error') or '') for j in jobs[-40:]],hide_index=True,use_container_width=True)
            st.caption('中断した処理は、状態を更新して同じ条件で再承認すると再計画します。保存済み成果物はハッシュ照合して再利用します。既存ジョブの復旧が必要な場合は下の制作画面から再開してください。')
        for label,menu,section in [('画像の手動取り込み・採用へ','イラスト制作','手動画像制作'),('ページ構成の確認へ','漫画ページ編集',None),('動画編集計画の承認へ','完成・書き出し','動画制作・MP4出力')]:
            if st.button(label): navigate(menu,section)
        outputs=[e for e in engine.studio.load()['exports'] if e.get('automation_key')]
        if outputs:
            with st.expander('自動処理の保存済みファイルを取得'):
                index=st.selectbox('取得する自動処理ファイル',range(len(outputs)),format_func=lambda i:outputs[i]['filename']+' / '+outputs[i]['saved_at'])
                if st.checkbox('選択ファイルのSHAを照合して取得'):
                    e=outputs[index]
                    st.download_button('自動処理ファイルをダウンロード',engine.studio.export_bytes(e),file_name=e['filename'])
    except (ValueError,OSError,StorageError) as error: st.error(str(error))
