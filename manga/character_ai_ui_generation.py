"""既存人物画面内の単発Gemini生成。設定画面・候補画面・正本は増やさない。"""
from manga.ui_runtime import ui as st
from manga.character_ai_ui import action
from manga.character_ai_generation import ERRORS,WAITING
from manga.story_ai_service import StoryAIService
from manga.story_ai_gemini import api_key

LABELS={'sending':'処理中・中断時は照合待ち','uncertain':'通信・請求の確認待ち','saved':'設定案を保存済み（未採用）',
        'invalid':'応答の確認が必要','stale':'元資料の再確認が必要','not_sent':'未送信で停止'}


def go_stage(prefix,stage):
    st.session_state[prefix+'_stage_pending']=stage;st.rerun()


def generation(service,project,version_id,enabled,prefix,names,new,navigate):
    mode=st.selectbox('Gemini人物生成の利用枠',('verified-free','paid'),format_func=lambda m:'確認済み無料API' if m=='verified-free' else '有料API',key=prefix+'_gemini_mode')
    st.info('人物抽出は無料・オフライン、設定案の生成だけをGemini 3.5へ1回送信します。無料モックとは異なります。実通信は開発中未検証です。')
    try:
        extraction=service.inspect_characters(version_id,names) if enabled else None
        if extraction:
            show_extraction(extraction)
            st.subheader('STEP 3：送信条件を確認し、人物設定案を1回生成')
        plan=service.preview_gemini(project,version_id,mode=mode,additional_names=names,new_request=new) if enabled else None
        if plan:
            request=plan['request'];card=request['character_ai']['card']
            st.write(f"モデル：{card['model']} ／ 対象：{len(request['character_ai']['extraction']['candidates'])}人 ／ 最大見積：{plan['maximum_jpy']}円")
            st.caption(f"最大入力：{card['max_input_tokens']:,} tokens ／ 最大出力（思考含む）：{card['max_output_tokens']:,} tokens ／ 料金確認日：{card['verified_on']}")
            st.caption('送る情報：正式脚本全文、抽出名・登場コマ・原文、保存済み人物の設定テキストとID、バイブルの設定・別名・固定項目・例外。基準画像、音声、画像パス、APIキーは本文へ送りません。')
            st.caption('共通の料金JSON・モデル設定を使用します。検索・ツール・キャッシュ・画像生成・有料への自動切替・自動リトライは使いません。思考minimalは完全OFFではなく、費用に含みます。')
            st.warning('無料枠も有料相当の最大額を共有予算へ予約します。アカウントの無料枠・残枠・データ利用条件は本人確認が必要です。')
            if plan['existing_request_id']:
                st.info('同じ元資料の人物生成履歴があります。下の履歴または候補確認から再利用してください。再送する場合だけ「同条件でも新しい人物提案を作る」を選び、個別承認します。')
            ready=enabled and StoryAIService.api_enabled(mode) and not plan['existing_request_id']
            waiting=any(r.get('character_ai',{}).get('provider')=='gemini-text' and r['character_ai']['state'] in WAITING for r in service.proposals.load()['requests'])
            if waiting:
                ready=False
                st.warning('以前の人物生成が処理中・保存または請求の照合待ちです。下の履歴から先に復旧してください。新しい送信は停止します。')
            try:api_key();st.caption('APIキー：設定あり（認証未検証・値は表示しません）')
            except ValueError:ready=False;st.warning('GEMINI_API_KEYが未設定、または形式の確認が必要です。起動するターミナルと既存の接続設定を確認してください。')
            if not StoryAIService.api_enabled(mode):st.caption('実送信は既定OFFです。無料確認済みはMANGA_ENABLE_TEXT_API、有料はMANGA_ENABLE_PAID_STORYの既存設定を確認してください。')
            if st.checkbox('人物生成の共有予算残額を確認する',key=prefix+'_gemini_budget'):
                summary=service.character_budget()
                st.write(f"作品残予算：{summary['project_remaining_jpy']}円 ／ 全作品月間残予算：{summary['monthly_remaining_jpy']}円")
                ready=ready and not summary['blocked'] and float(plan['maximum_jpy'])<=min(summary['project_remaining_jpy'],summary['monthly_remaining_jpy'])
                if summary['blocked']:st.warning('共通予算に請求確認待ちがあります。API予算管理で照合するまで生成できません。')
            else:ready=False;st.caption('送信前に共有予算の残額を確認してください。')
            consent=st.checkbox('この人物設定の1送信だけ、モデル・送信内容・利用条件・最大費用を確認して承認する',key=prefix+'_'+plan['approval_key']+'_consent',disabled=not ready)
            if st.button('Gemini 3.5で人物設定案を1回生成',type='primary',disabled=not ready or not consent):
                def send():
                    result=service.generate_gemini(project,version_id,approval=plan['approval_key'],expected_revision=plan['revision'],mode=mode,additional_names=names,new_request=new)
                    meta=result['request']['character_ai']
                    if meta['state']!='saved':raise ValueError(ERRORS.get(meta['error'],'人物生成の保存・請求を確認してください。'))
                action(send,'Geminiの人物設定案を保存しました。正式人物は未変更です。次はSTEP 4で修正・部分採用してください。')
    except (ValueError,TypeError) as error:st.info(str(error))
    if navigate:
        if st.button('既存のGemini接続・料金設定を開く'):
            st.session_state['story_ai_'+project.id+'_view']='制作条件';navigate('企画・ストーリー','AI企画・ストーリー')
        if st.button('人物生成の請求明細・手動照合へ'):navigate('完成・書き出し','API予算管理')
    history=[r for r in reversed(service.proposals.load()['requests']) if r.get('character_ai',{}).get('provider')=='gemini-text']
    if history:
        st.markdown('**Gemini人物生成の保存・実行履歴**')
        selected=st.selectbox('保存・請求を確認する人物生成',list(range(len(history))),format_func=lambda i:history[i]['saved_at']+' / '+LABELS[history[i]['character_ai']['state']]+(' / 過去の脚本版' if history[i]['story_version_id']!=version_id else ''),key=prefix+'_gemini_history')
        row=history[selected];meta=row['character_ai'];st.write(LABELS[meta['state']])
        st.caption('照合用リクエストID：'+row['id'])
        if meta['error']:st.warning(ERRORS[meta['error']])
        if st.button('人物生成の保存を再開・請求を照合（再送なし）'):
            def resume():
                result=service.recover_gemini(row['id'])
                if result['request']['character_ai']['state']!='saved':raise ValueError(ERRORS[result['request']['character_ai']['error']])
            action(resume,'保存済み応答と請求を照合しました。APIは再送していません。')
        if meta['state']=='saved':
            if row['story_version_id']==version_id:
                if st.button('STEP 4：人物候補の修正・採用へ'):go_stage(prefix,'候補確認・採用')
            else:st.info('これは過去の脚本版の提案です。STEP 1で同じ版を選ぶと閲覧できます。現在の脚本への採用や自動適用はしません。')


def show_extraction(extraction):
    st.subheader('STEP 2：抽出名・登場コマ・照合候補を確認')
    st.dataframe([dict(人物=c['name'],登場コマ=' / '.join(f'{p}ページ{n}コマ' for p,n in sorted({(s['page'],s['panel']) for s in c['sources']})),
        既存人物の候補=' / '.join(m['name'] for m in c['matches']) or '未同定',状態='人物の同定は確認待ち') for c in extraction['candidates']],hide_index=True)
    st.caption('オフライン抽出は明確な話者・登録名・追加名を使用します。既存人物も自動選択しません。登場コマは脚本の記述根拠であり、外見の確認結果ではありません。')
    if extraction['unknown_speakers']:st.warning(f"曖昧な話者が{len(extraction['unknown_speakers'])}件あります。既知人物へ割り当てず確認待ちにします。")
