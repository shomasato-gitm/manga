"""ページ単位の生成対象確認・読み編集・試聴。原寸音声は選択時だけ読む。"""
import sqlite3

from manga.budget.config import open_budget
from manga.ui_runtime import ui as st
from manga.storage import StorageError
from manga.voice_models import EMOTIONS
from manga.voice_ui import act


def generation_view(service,data,plan,prefix):
    if not plan['rows']:st.info('セリフ・ナレーションがありません。');return
    pages=sorted({r['page'] for r in plan['rows']})
    page=st.selectbox('音声のページ',pages,key=prefix+'_page')
    all_pages=st.checkbox('生成対象を全ページから選択',key=prefix+'_all_pages')
    rows=plan['rows'] if all_pages else [r for r in plan['rows'] if r['page']==page]
    st.dataframe([{'ページ':r['page'],'コマ':r['panel'],'話者':r['speaker_name'] or '要確認','内容':r['text'],
                   '状態':'要確認' if r['needs_review'] else r['status']} for r in rows],hide_index=True,use_container_width=True)
    by_id={r['id']:r for r in rows}
    label=lambda k:f"{by_id[k]['page']}ページ{by_id[k]['panel']}コマ：{by_id[k]['speaker_name'] or '要確認'}／{by_id[k]['text'][:25]}"
    identifier=st.selectbox('確認・編集する発話',list(by_id),format_func=label,key=f'{prefix}_{page}_{all_pages}_utterance')
    row=by_id[identifier]
    for warning in row['warnings']:st.warning(warning)
    with st.expander('話者・読み・発話ごとの感情を編集',expanded=row['needs_review']):
        speakers=service.speakers(plan['artwork']);options=[None,*speakers]
        speaker=st.selectbox('この発話の話者',options,index=options.index(row['speaker_id']) if row['speaker_id'] in options else 0,
                            format_func=lambda v:speakers.get(v,'要確認'),key=f'{prefix}_{identifier}_{row["text_hash"]}_speaker')
        text=st.text_area('読み上げ文・読み（字幕の原文は保持）',value=row['spoken_text'],key=f'{prefix}_{identifier}_{row["text_hash"]}_reading')
        default=row.get('settings') or {}
        emotion=st.selectbox('この発話の感情',list(EMOTIONS),index=list(EMOTIONS).index(default.get('emotion','normal')),format_func=EMOTIONS.get,key=f'{prefix}_{identifier}_emotion')
        confirmed=st.checkbox('話者と読み上げ文を確認した',key=f'{prefix}_{identifier}_review')
        if st.button('発話設定を保存',disabled=not confirmed or speaker is None):
            act(lambda:service.save_edit(identifier,speaker,text,emotion,expected_revision=data['revision'],source_token=plan['token']),'発話設定を保存しました。元の脚本・字幕原文は変更していません。')
    if row['asset'] and st.checkbox('選択した音声を試聴・WAV取得',key=f'{prefix}_{identifier}_preview'):
        try:
            content=service.audio_bytes(row['asset'])
            st.audio(content,format='audio/wav')
            st.download_button('この音声のWAVをダウンロード',content,file_name=f"{identifier}.wav",mime='audio/wav')
            reviewed=st.checkbox('試聴して人物・読み・感情を確認した',key=f'{prefix}_{row["asset"]["id"]}_heard')
            if st.button('この音声を確認済みにする',disabled=not reviewed):
                act(lambda:service.review_audio(row['asset']['id'],expected_revision=data['revision']),'人間による確認を記録しました。')
        except (ValueError,OSError,StorageError) as error:st.error(str(error))
    st.subheader('生成する発話を選ぶ')
    selected=st.multiselect('音声生成対象',list(by_id),default=[r['id'] for r in rows if not r['needs_review'] and not r['asset']],format_func=label,key=f'{prefix}_{page}_{all_pages}_{data["settings_revision"]}_targets')
    regenerate=st.checkbox('既存音声を残して再生成する（有料APIでは再課金）',key=prefix+'_regenerate')
    if not selected:st.info('生成対象を選択してください。保存済み音声はAPIなしで再利用できます。');return
    try:estimate=service.estimate(selected,regenerate=regenerate)
    except (ValueError,StorageError) as error:st.info(str(error));return
    st.write(f"事前上限見積：{estimate['max_jpy']}円 ／ 有料API予定：{estimate['paid_calls']}回")
    st.caption('無料枠を見込まない予算換算額です。実際の請求書を取得した金額ではありません。確認用モックは人の声ではないトーンです。')
    approval=None;enabled=True
    if estimate['paid_calls']:
        enabled=service.paid_enabled() and plan['confirmed']
        try:
            summary=open_budget(service.directory).summary(service.project_id)
            st.write(f"作品API残額：{summary['project_remaining_jpy']}円 ／ 月間残額：{summary['monthly_remaining_jpy']}円 ／ 請求額不明：{summary['uncertain_count']}件")
            if summary['blocked']:enabled=False;st.warning('請求不明・見積超過の確認が必要です。新しいAPI呼び出しを停止しています。')
        except (ValueError,OSError,sqlite3.Error):
            enabled=False;st.error('予算台帳の保存先・状態を確認してください。初期化して再試行しないでください。')
        if not enabled:st.warning('有料実行は無効、または脚本が未確定です。')
        accepted=st.checkbox('今回の対象と上限見積を確認し、有料API呼び出しを明示承認する',disabled=not enabled,key=prefix+'_'+estimate['approval_key']+'_consent')
        enabled=enabled and accepted
        if enabled:approval=estimate['approval_key']
    bar=st.empty()
    def progress(done,total):bar.progress(done/total,text=f'音声処理 {done}/{total}件（キャッシュ再利用を含む）')
    if st.button('選択した音声を生成・保存／キャッシュ再利用',type='primary',disabled=not enabled):
        act(lambda:service.generate(selected,regenerate=regenerate,approval=approval,progress=progress),'音声を保存しました。試聴後に字幕・タイムラインへ進んでください。')
