"""一覧と制作ガイド。実際の編集・採用・保存は既存画面に委譲する。"""
from manga.ui_runtime import ui as st
from manga.ux import header,thumbnail_card,status,details,empty,jump
from manga.artwork_storage import ArtworkStore
from manga.storage import StorageError


def characters(project,directory,navigate):
    header('キャラクター','人物を選んで設定を編集し、基準画像とバイブルを確認します。')
    store=ArtworkStore(directory,project.id)
    try: people=[c for c in store.load()['characters'] if not c['deleted_at']]
    except StorageError as error: st.error(str(error)); return
    if not people: empty('人物は未登録です。まず1人目の設定を作りましょう。')
    if st.button('人物を新しく登録',type='primary'):
        st.session_state['art_'+project.id+'_character_pending']=None
        navigate('キャラクター・作風','手動設定')
    for offset in range(0,len(people),3):
        for col,person in zip(st.columns(3),people[offset:offset+3]):
            with col:
                with st.container(border=True):
                    image=next((r for r in person['references'] if r['id']==person['primary_reference_id']),None)
                    thumbnail_card(store,image,person['name'])
                    st.subheader(person['name']); st.caption(person.get('role') or '役割は未設定')
                    features=' / '.join(person.get(k) or '未設定' for k in ('hair_color','hairstyle','clothing'))
                    st.write(features[:180]+('…' if len(features)>180 else ''))
                    filled=sum(bool(person.get(k)) for k in ('name','role','hair_color','hairstyle','clothing','personality'))
                    st.caption(f'プロフィール入力 {filled}/6項目（品質の承認ではありません）')
                    status('基準画像あり・外見は要確認' if image else '基準画像なし')
                    if st.button('プロフィールを編集',key='ux_person_'+person['id']):
                        st.session_state['art_'+project.id+'_character_pending']=person['id']
                        navigate('キャラクター・作風','手動設定')
                    if st.button('この人物の基準画像',key='ux_refs_'+person['id']):
                        st.session_state['art_'+project.id+'_reference_character_pending']=person['id']
                        navigate('キャラクター・作風','基準画像・作風')
                    if st.button('この人物のバイブル',key='ux_bible_'+person['id']):
                        st.session_state['character_ai_'+project.id+'_bible_person']=person['id']
                        jump('キャラクター・作風','AIキャラクター制作・一貫性管理',navigate)
                    with details(): st.caption('人物UUID：'+person['id'])


def panels(project,directory,navigate):
    header('ページ・コマ一覧','画像を選んで、描写の確認から画像の取り込み・比較・正式採用まで進めます。')
    from manga.production_dashboard import dashboard
    from manga.gallery_ui import page_items
    try:
        result=dashboard(project,directory); store=ArtworkStore(directory,project.id); art=store.load()
        rows=result['panels']; people={c['id']:c['name'] for c in art['characters']}
        if not rows:
            empty('正式脚本を保存するとコマが表示されます。')
            if st.button('脚本を取り込む',type='primary'): navigate('企画・ストーリー','生成・取り込み')
            return
        page=st.selectbox('一覧に表示するページ',[None]+sorted({r['page'] for r in rows}),format_func=lambda n:'全ページ' if n is None else f'{n}ページ')
        pending=st.checkbox('未完成・要再確認のコマだけ',value=False)
        visible=[r for r in rows if (page is None or r['page']==page) and (not pending or not r['selected'] or r['stale'])]
        st.caption(f'表示対象 {len(visible)} / 全{len(rows)}コマ。サムネイルは表示中の最大12コマだけを読み込みます。')
        visible=page_items(visible,'ux_panel_grid','コマ一覧',size=12)
        for offset in range(0,len(visible),3):
            for col,row in zip(st.columns(3),visible[offset:offset+3]):
                with col:
                    with st.container(border=True):
                        entry=art['panels'].get(row['key'],{})
                        image=next((c for c in entry.get('candidates',[]) if c['id']==entry.get('selected_image_id')),None)
                        if image is None and entry.get('candidates'): image=entry['candidates'][-1]
                        title=f"{row['page']}ページ・{row['panel']}コマ"
                        thumbnail_card(store,image,title)
                        st.markdown('**'+title+'**'); status('要再確認' if row['stale'] else row['status'])
                        st.caption('登場人物：'+'、'.join(people.get(i,'削除済み人物') for i in (entry.get('direction') or {}).get('character_ids',[])))
                        if st.button('このコマを制作',type='primary',key='ux_panel_'+row['key']):
                            st.session_state['manual_'+project.id+'_jump']=(row['page'],row['panel'])
                            # 採用済みのコマもグリッドから直接編集できる。
                            st.session_state['manual_'+project.id+'_unfinished_only']=False
                            navigate('イラスト制作','手動画像制作')
    except (ValueError,OSError,StorageError) as error: st.error(str(error))


def media(project,directory,navigate):
    header('音声・動画','音声を準備して確認し、字幕を保存したら動画へ進みます。')
    for title,text,section in [('1. 音声と字幕','話者設定 → 無料音声生成 → 試聴・確認 → 字幕・タイムライン','音声制作・字幕'),('2. 動画を作る','形式選択 → 編集・プレビュー → 素材検査 → MP4生成 → 再生','動画制作・MP4出力')]:
        with st.container(border=True):
            st.subheader(title); st.write(text)
            if st.button(title+'を開く',type='primary'): navigate('完成・書き出し',section)
    st.info('画像の正式採用が不足している場合は、画像制作で確認・採用してください。無料トーンは完成した日本語音声ではありません。')
    if st.button('不足する画像を確認'): navigate('イラスト制作','ページ・コマ一覧')


def settings(project,directory,navigate):
    header('設定・管理','作品・保存先・予算・履歴を確認します。同期や復旧は内容を確認してから実行してください。')
    from manga.ux import SHORTCUTS
    for label,menu,section in SHORTCUTS['設定・管理']:
        with st.container(border=True):
            st.subheader(label)
            if st.button(label+'を開く',key='ux_manage_'+section): navigate(menu,section)
