"""利用者が選んだ時だけ制作状況と計画を計算する。料金は参考と上限を分ける。"""
import json

from manga.image_ai_models import SIZES
from manga.image_ai_ui import action
from manga.ui_runtime import ui as st
from manga.voice_models import digest


def overview(service,prefix,navigate):
    st.subheader('全ページの画像準備状況')
    if st.button('全コマの制作計画を確認・更新'):
        def build(): st.session_state[prefix+'_overview']=service.overview()
        action(build,'画像準備状況を更新しました。画像の外見を解析した結果ではありません。')
    rows=st.session_state.get(prefix+'_overview')
    if rows:
        st.dataframe([dict(ページ=r['page'],コマ=r['panel'],状態=r['status'],候補数=r['candidates'],確認事項=r['reason']) for r in rows],hide_index=True,use_container_width=True)
        st.caption('これは更新ボタンを押した時点の一覧です。画像・設定を変更した後は更新してください。')
    else: st.caption('一覧は未計測です。必要時だけ更新し、原寸画像を全件読み込みません。')
    if navigate and st.button('基準画像・作風の既存画面へ'): navigate('キャラクター・作風','基準画像・作風')


def plan_editor(service,prefix,navigate):
    st.subheader('生成する対象を一つ選ぶ')
    art=service.artwork.load(); story=service.store.stories.load_history()[-1]['story']
    kind=st.radio('計画の画像種類',('character','panel'),format_func=lambda k:'人物の基準画像' if k=='character' else '漫画のコマ画像',horizontal=True,key=prefix+'_kind')
    selection={}
    if kind=='character':
        people={c['id']:c for c in art['characters'] if not c['deleted_at']}
        if not people: st.info('先に人物を正式採用してください。'); return
        selection['character_id']=st.selectbox('基準画像を作る人物',list(people),format_func=lambda i:people[i]['name']+' / '+i[:8],key=prefix+'_person')
        selection['view']=st.selectbox('基準画像の向き・種類',['正面','斜め','全身','表情差分'],key=prefix+'_reference_view')
        selection['expression']=st.text_input('基準画像の表情（任意）',value='自然な表情',max_chars=500,key=prefix+'_expression')
    else:
        selection['page']=st.selectbox('画像制作のページ',[p['page'] for p in story['pages']],key=prefix+'_page')
        selection['panel']=st.selectbox('画像制作のコマ',[c['panel'] for c in story['pages'][selection['page']-1]['panels']],key=prefix+'_'+str(selection['page'])+'_panel')
    size=st.selectbox('候補の画像サイズ',list(SIZES),key=prefix+'_size')
    options=dict(size=size)
    with st.expander('構図・横型／縦型への転用設定（任意）'):
        options['composition_note']=st.text_area('構図の補足',max_chars=2000,key=prefix+'_composition_note')
        options['focus']={k:st.number_input(label,min_value=0.0,max_value=1.0,value=v,step=0.05,key=prefix+'_focus_'+k) for k,label,v in [('x','重要部分の中心X',0.5),('y','重要部分の中心Y',0.5),('width','重要部分の幅',0.6),('height','重要部分の高さ',0.7)]}
        options['margin']=st.text_input('字幕・見切れ防止の余白指示',value='顔・手・重要物を外縁から離し、字幕用の余白を確保',max_chars=1000,key=prefix+'_margin')
    signature=digest(dict(kind=kind,selection=selection,options=options))
    if st.button('この対象の生成計画を作成',type='primary'):
        def build(): st.session_state[prefix+'_plan']=dict(signature=signature,plan=service.plan(kind,options=options,**selection))
        action(build,'生成計画を作成しました。「生成・手動取り込み」で依頼を保存してください。正規画像はまだ変更していません。')
    saved=st.session_state.get(prefix+'_plan')
    if saved and saved['signature']==signature:
        plan=saved['plan']; st.success('計画作成済み：'+plan['story_version_id'][:8]+' / 参照画像'+str(len(plan['reference_images']))+'枚 / API送信0回')
        for warning in plan['warnings']: st.warning(warning)
        with st.expander('プロンプト・人物UUID・バイブル・参照画像SHAの詳細'): st.json(plan,expanded=False)
        from manga.prompt_ui import prompt_tools
        prompt_tools(plan['prompt'],prefix+'_plan_prompt','image_generation_prompt.txt',response_json=False,next_step='参照画像は自分でChatGPT等へ手動添付してください。画像を生成・保存後、手動取り込みへ進みます。アプリからは送信していません。')
        if plan['reference_images'] and st.checkbox('添付する参照画像を1枚確認・取得',key=prefix+'_show_reference'):
            refs={r['id']:r for r in plan['reference_images']}
            selected=st.selectbox('取得する参照画像',list(refs),key=prefix+'_download_reference')
            from manga.character_ai_assets import reference_path
            from manga.video_sources import file_bytes
            from manga.gallery_ui import thumbnail
            import hashlib
            ref=refs[selected]; reference_path(service.store.root,ref)
            payload=file_bytes(service.store.root,ref['path'],20*1024*1024)
            if hashlib.sha256(payload).hexdigest()!=ref['sha256']: raise ValueError('参照画像のSHAが変わりました。計画を再作成してください。')
            st.image(thumbnail(service.artwork,ref),width=250)
            st.download_button('参照画像PNGをダウンロード',payload,file_name=selected+'.png',mime='image/png')


def estimate(service,prefix):
    from manga.image_ai_pricing import research,output_reference
    raw,expired=research()
    st.subheader('モデル・費用の確認（実送信不可）')
    st.caption('公式再取得日：'+raw['verified_on']+'。ChatGPTの月額契約・Google AI StudioのUI試用はAPI無料クレジットではありません。')
    if expired: st.warning('料金確認日が30日を超えているか不正です。再確認が必要です。')
    cards={c['model']:c for c in raw['cards']}
    model=st.selectbox('比較する画像モデル（送信不可）',list(cards),key=prefix+'_research_model')
    card=cards[model]
    st.error('最大総請求量を確認できないため、有料APIは未対応です。承認・キー・残額があっても予約・送信しません。')
    count=st.number_input('画像出力部分だけを比較する参考枚数',min_value=1,max_value=600,value=27,key=prefix+'_cost_count')
    fx=st.number_input('参考為替（円/USD・実為替ではない）',min_value=1.0,max_value=10000.0,value=160.0,key=prefix+'_fx')
    subtotal=output_reference(card,count,str(fx))
    if subtotal is not None: st.write(f'Vertex Globalの画像出力だけの参考小計：約{subtotal:.2f}円（税1.10の仮定）。Gemini Developer APIの見積・最大額ではありません。')
    else: st.write('単価は公式資料で確認しましたが、1枚の最大token数が未確認のため円の上限を表示しません。')
    st.caption('入力テキスト・参照画像・reasoning・再生成・他工程の費用を含めず、500円で完成すると保証しません。')
    with st.expander('料金の根拠・確認状態'): st.json(card,expanded=False)
    if st.button('同じ共有台帳の残額を確認・更新'):
        from manga.budget.config import open_budget
        def load():
            summary=open_budget(service.directory).summary(service.project_id)
            st.session_state[prefix+'_budget']=dict(project=str(summary['project_remaining_jpy']),monthly=str(summary['monthly_remaining_jpy']),blocked=summary['blocked'])
        action(load,'共有予算の残額を確認しました。新しい予約・画像API送信は0です。')
    if prefix+'_budget' in st.session_state:
        summary=st.session_state[prefix+'_budget']
        st.write(f"確認時点の残額：作品{summary['project']}円／全作品月間{summary['monthly']}円。請求確認待ち：{summary['blocked']}")
    st.info('無料モック・手動取り込みは0円です。有料送信を将来追加する場合も、同じ作品生涯500円／月間5,000円とBudgetedExecutorを使用します。')
