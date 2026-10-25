"""Streamlitの公開コンポーネントだけを使う共通UI。作品データを保存しない。"""
from manga.ui_runtime import ui as st

PRIMARY = {
    'ホーム':('企画・ストーリー','ホーム・進行状況'),
    '企画・脚本':('企画・ストーリー','AI企画・ストーリー'),
    'キャラクター':('キャラクター・作風','人物一覧'),
    '画像制作':('イラスト制作','ページ・コマ一覧'),
    '漫画編集':('漫画ページ編集',None),
    '音声・動画':('完成・書き出し','音声・動画ガイド'),
    '設定・管理':('完成・書き出し','設定・管理ガイド'),
}
SHORTCUTS = {
    '企画・脚本':[('企画を比較','企画・ストーリー','AI企画・ストーリー'),('脚本を編集','企画・ストーリー','ページ編集'),('脚本を確定','企画・ストーリー','比較・脚本確定'),('JSONを取り込む','企画・ストーリー','生成・取り込み')],
    'キャラクター':[('人物一覧','キャラクター・作風','人物一覧'),('人物を登録・編集','キャラクター・作風','手動設定'),('基準画像','キャラクター・作風','基準画像・作風'),('バイブルを確認','キャラクター・作風','AIキャラクター制作・一貫性管理')],
    '画像制作':[('全コマを見る','イラスト制作','ページ・コマ一覧'),('手動画像制作','イラスト制作','手動画像制作'),('作画指示','絵コンテ・作画指示','コマ一覧・制作へ'),('画像の履歴・復旧','イラスト制作','AI画像生成・採用')],
    '漫画編集':[('PNG・PDFを書き出す','完成・書き出し','PNG・PDF書き出し'),('品質を確認','完成・書き出し','品質チェック・修正管理')],
    '音声・動画':[('音声を作る','完成・書き出し','音声制作・字幕'),('動画を作る','完成・書き出し','動画制作・MP4出力')],
    '設定・管理':[('作品を管理','企画・ストーリー','プロジェクト管理'),('保存先・復旧診断','完成・書き出し','保存先・容量・診断'),('予算を確認','完成・書き出し','API予算管理'),('GitHub作品同期','完成・書き出し','GitHub作品同期')],
}


def detailed(): return st.session_state.get('ux_mode','標準モード')=='詳細モード'


def header(title,description):
    st.header(title); st.caption(description)


def status(value):
    st.markdown('**状態：'+value+'**')


def empty(message,action=None):
    st.info(message)
    if action: action()


def details(title='詳細情報'):
    return st.expander(title,expanded=detailed())


def progress_card(title,done,total,state):
    st.markdown('**'+title+'**'); status(state)
    st.progress(min(1.0,float(done)/total) if total else 0.0,text=f'{done} / {total}')


def thumbnail_card(store,image,caption):
    if not image: st.caption('画像はまだありません'); return
    try:
        from manga.gallery_ui import thumbnail
        st.image(thumbnail(store,image),caption=caption,width=180)
    except (ValueError,OSError) as error: st.warning(str(error))
    except Exception as error:
        from manga.storage import StorageError
        if isinstance(error,StorageError): st.warning(str(error))
        else: raise


def group(menu,section):
    if section=='ホーム・進行状況': return 'ホーム'
    if section in ('プロジェクト管理','API予算管理','保存先・容量・診断','GitHub作品同期','設定・管理ガイド'): return '設定・管理'
    if section in ('音声制作・字幕','動画制作・MP4出力','音声・動画ガイド'): return '音声・動画'
    if menu=='企画・ストーリー': return '企画・脚本'
    if menu=='キャラクター・作風': return 'キャラクター'
    if menu in ('絵コンテ・作画指示','イラスト制作'): return '画像制作'
    return '漫画編集'


def jump(menu,section,navigate):
    project=st.session_state.get('active_project')
    if section=='AIキャラクター制作・一貫性管理' and project:
        st.session_state['character_ai_'+project.id+'_view']='キャラクターバイブル'
    if section=='AI画像生成・採用' and project:
        st.session_state['image_ai_'+project.id+'_view']='履歴・復旧'
    navigate(menu,section)


def shell(menu,section,project,navigate):
    current=group(menu,section)
    st.caption(f"{project.title if project else '作品未選択'} / {current} / {section or 'ページ編集'}")
    if project and current!='ホーム':
        st.session_state.setdefault('_ux_last_screen',{})[project.id]=(menu,section)
    shortcuts=SHORTCUTS.get(current,[])
    if shortcuts:
        columns=st.columns(min(4,len(shortcuts)))
        for i,(label,m,s) in enumerate(shortcuts):
            if columns[i%len(columns)].button(label,key='ux_short_'+label,type='primary' if (menu,section)==(m,s) else 'secondary'):
                jump(m,s,navigate)
    if current=='ホーム': return
    keys=list(PRIMARY); index=keys.index(current)
    left,right=st.columns(2)
    if index and left.button('← 前の工程：'+keys[index-1],key='ux_previous'): navigate(*PRIMARY[keys[index-1]])
    if index<len(keys)-1 and right.button('次の工程：'+keys[index+1]+' →',key='ux_next'): navigate(*PRIMARY[keys[index+1]])


def wizard(label,steps,current,set_step):
    st.caption(label+'：'+' → '.join(steps))
    columns=st.columns(min(3,len(steps)))
    for i,step in enumerate(steps):
        if columns[i%len(columns)].button(f'{i+1}. {step}',key=label+'_'+step,type='primary' if current==step else 'secondary'):
            set_step(step); st.rerun()
