"""手動生成の対象・プロンプト・基準画像・取り込み・比較を1画面へつなぐ。"""
import hashlib
import json

from manga.ui_runtime import ui as st
from manga.image_ai_service import ImageAIService
from manga.image_ai_ui_candidates import candidates
from manga.storage import StorageError
from manga.voice_models import digest


def receipt(plan):
    return {k:plan[k] for k in ('kind','character_id','page','panel','story_version_id','material_token','prompt_sha256','bible_ids')} | {
        'character_ids':[c['id'] for c in plan['source']['characters']]}


def verify_receipt(plan, raw):
    if len(raw) > 64*1024:
        raise ValueError('対象確認票は64KiB以内にしてください。')
    try:
        value = json.loads(raw)
    except (ValueError,UnicodeError) as error:
        raise ValueError('対象確認票をJSON形式にしてください。') from error
    if value != receipt(plan):
        raise ValueError('ページ・コマ・人物UUID・脚本版・プロンプトが対象確認票と一致しません。正しい対象の画像を選び直してください。')
    return True


def _run(function, message, *, page=None, panel=None):
    """入力は保持し、利用者向けの対処と内部詳細を分けて表示する。"""
    from manga.manual_image_steps import guidance
    from manga.ux import details
    try:
        st.session_state['_operation_busy'] = True
        with st.spinner('入力と保存状態を検証しています…'):
            function()
        st.session_state['_action_epoch'] = st.session_state.get('_action_epoch', 0)+1
        st.session_state['_image_ai_notice'] = message
        st.rerun()
    except (ValueError, OSError, StorageError) as error:
        st.error(guidance(error,page=page,panel=panel))
        with details('エラーの詳細・復旧情報'):
            st.write(str(error))
    finally:
        st.session_state['_operation_busy'] = False


def _requirements(service, selection, navigate, result):
    from manga.character_ai_service import CharacterAIService
    from manga.image_ai_planning import materials, target
    from manga.manual_image_steps import guidance
    try:
        _, _, token = materials(CharacterAIService(service.directory,service.project_id),target('panel',**selection))
        return token
    except (ValueError, OSError, StorageError) as error:
        st.warning(guidance(error,**selection))
        # 具体的な不足条件は既存ダッシュボードと同じ保存値から取得する。
        keys = ('finalized','characters','directions')
        for row in result['rows']:
            if row['key'] not in keys:
                continue
            for reason in row['missing']:
                if row['key'] == 'directions' and not reason.startswith(f"{selection['page']}ページ{selection['panel']}コマ："):
                    continue
                st.write('・'+reason)
        if st.button('脚本の確定画面へ'):
            navigate('企画・ストーリー','比較・脚本確定')
        if st.button('人物・基準画像・バイブルを確認する'):
            st.session_state['character_ai_'+service.project_id+'_view'] = 'キャラクターバイブル'
            navigate('キャラクター・作風','AIキャラクター制作・一貫性管理')
        return None


def _progress(rows, selected, prefix):
    good = lambda r: r['selected'] and not r['stale'] and r['prompt_ready']
    count = sum(bool(good(r)) for r in rows)
    unfinished = [r for r in rows if not good(r)]
    stale = sum(bool(r['stale']) for r in rows)
    st.progress(count/len(rows),text=f'全{len(rows)}コマ中 {count}コマが現在の素材で正式採用済み')
    st.caption(f'現在の対象：{selected.replace(":","ページ・")}コマ ｜ 未完成 {len(unfinished)}コマ ｜ 要再確認 {stale}コマ')
    keys = [r['key'] for r in rows]
    index = keys.index(selected)
    ordered = rows[index+1:]+rows[:index+1]
    following = next((r for r in ordered if not good(r)),None)
    if following and following['key'] != selected:
        st.info(f"次の未完成コマ：{following['page']}ページ・{following['panel']}コマ")
    st.caption('テスト素材の採用件数は操作の進捗です。人物一致・素材権利・公開品質や作品全体の完成を保証しません。')
    return following


def _next(following, selected, prefix, *, bottom=False):
    enabled = bool(following and following['key'] != selected)
    label = '次の未完成コマへ進む' if bottom else '次に画像が必要なコマへ'
    if st.button(label,type='primary' if bottom else 'secondary',disabled=not enabled):
        st.session_state[prefix+'_jump'] = (following['page'],following['panel'])
        st.rerun()


def _inline_direction(service, art, latest, source_panel, row, prefix):
    from manga.direction_drafts import script_hints, _aliases
    from manga.panel_direction_ui import direction_editor
    from manga.page_storage import PageStore
    from manga.page_prompt_hints import layout_hint
    page, panel = row['page'], row['panel']
    direction = (art['panels'].get(row['key']) or {}).get('direction') or {}
    st.subheader('STEP 2：作画指示を入力・保存')
    if not direction:
        st.info(f'{page}ページ{panel}コマ目の作画指示が未保存です。画像生成プロンプトには保存済み作画指示が必要です。下の入力欄で確認・保存してください。')
    elif row['direction_stale']:
        st.warning('以前の脚本版の作画指示です。新しい脚本へ自動適用しません。内容と登場人物を確認・修正し、再確認を承認して保存してください。')
    hints = next(r for r in script_hints(latest,art,_aliases(service.artwork)) if r['key']==row['key'])
    with st.expander('正式脚本からの入力補助・根拠'):
        st.write('人物の候補：'+' / '.join(hints['evidence']))
        for key,label in (('location','場所'),('time_of_day','時間帯'),('expression','表情'),('pose','ポーズ'),('composition','構図'),('background','背景'),('aspect_ratio','画像アスペクト比')):
            st.write(label+'：'+hints['hints'].get(key,'明記なし。必要に応じて入力してください。'))
        for reason in hints['issues']:
            st.warning(reason)
    page_data = latest['story']['pages'][page-1]
    st.caption(layout_hint(page_data,PageStore(service.directory,service.project_id).load(),panel).split('\n')[0])
    def save(function,message):
        _run(function,message,page=page,panel=panel)
    # 既存の編集部品を利用し、人物候補・明記情報も未保存の入力として扱う。
    return direction_editor(service.artwork,art,latest,page,source_panel,direction,row,prefix+'_direction',save,
                            suggestions=hints['fields'],require_confirmation=row['direction_stale'])


def _package(service,plan,prefix,identity,art,run):
    from manga.prompt_ui import prompt_tools
    from manga.ux import details
    prompt_tools(plan['prompt'],prefix+'_'+identity+'_prompt','manual_prompt.txt',response_json=False)
    with details('対象確認票の詳細・内部ID'):
        st.json(receipt(plan),expanded=False)
    st.download_button('対象確認票をダウンロード',json.dumps(receipt(plan),ensure_ascii=False,indent=2).encode(),file_name=f"manual_p{plan['page']}_c{plan['panel']}_{plan['story_version_id']}.json",mime='application/json')
    for ref in plan['reference_images']:
        from manga.video_sources import file_bytes
        content = file_bytes(service.store.root,ref['path'],20*1024*1024)
        if hashlib.sha256(content).hexdigest() != ref['sha256']:
            raise ValueError('基準画像が変更されました。準備をやり直してください。')
        name = next((c['name'] for c in art['characters'] if c['id']==ref['character_id']),'人物')
        with st.expander(name+' / '+ref.get('view','基準画像')):
            st.image(content,width=180)
            st.download_button(name+'の基準画像PNGをダウンロード',content,file_name=ref['id']+'.png',mime='image/png',key=prefix+'_download_'+ref['id'])
    st.subheader('STEP 4：ChatGPT等で画像を制作（外部での手動操作）')
    st.write('プロンプトをコピーし、必要な基準画像をChatGPT等へ自分で添付してください。対象確認票は対応する画像と一緒に保管し、制作が終わったら下へ戻してください。アプリから外部へは送信しません。')
    st.subheader('STEP 5：画像と対象確認票を取り込む')
    upload = st.file_uploader('手動生成画像',type=['png','jpg','jpeg','webp'],key=prefix+'_'+identity+'_upload')
    manifest = st.file_uploader('生成時の対象確認票（必須）',type=['json'],key=prefix+'_'+identity+'_receipt')
    st.caption('確認票のページ・コマ・人物UUID・脚本版・プロンプトと、画像の形式・SHAを照合します。画像の外見・人物一致・権利は次の人間確認が必要です。')
    upload_sha = hashlib.sha256(upload.getvalue()).hexdigest() if upload else None
    if upload_sha:
        with details('アップロード画像のSHA'):
            st.caption('取り込み対象のSHA-256：'+upload_sha)
    if not upload or not manifest:
        st.caption('画像と制作時の対象確認票の両方を選ぶと、取り込みボタンを利用できます。')
    if st.button('検証して画像候補を取り込む',disabled=upload is None or manifest is None):
        def import_image():
            from manga.automation import Automation
            Automation(service.directory,service.project_id).import_manual(plan,manifest.getvalue(),upload.getvalue(),upload.name,upload_sha)
        run(import_image,'候補を保存しました。STEP 6で外見と権利を確認し、別操作で正式採用してください。')


def render_manual_images(project, directory, navigate):
    st.header('手動画像制作')
    st.caption('案内に沿って1コマずつ制作します。外部で作った画像を手動で戻すため、画像APIへの送信は0回・0円です。')
    st.caption('保存した作画指示に脚本・人物バイブル・基準画像を組み合わせてプロンプトを準備します。対象確認票は、その素材と画像の対応を照合するためのファイルです。取り込んだ画像は候補となり、確認と正式採用は別操作です。')
    if '_image_ai_notice' in st.session_state:
        st.success(st.session_state.pop('_image_ai_notice'))
    service = ImageAIService(directory,project.id)
    prefix = 'manual_'+project.id
    try:
        data = service.store.load()
        from manga.production_dashboard import recovery_routes
        other_recoveries = [r for r in recovery_routes(directory,project.id) if r['section'] not in ('AI画像生成・採用','音声制作・字幕','動画制作・MP4出力')]
        if other_recoveries:
            item = other_recoveries[0]
            st.warning(item['label']+'の保存が途中です。既存の復旧画面で再開してください。')
            if st.button('保存途中の制作画面へ'):
                navigate(item['menu'],item['section'])
            return
        if service.store.pending():
            st.warning('画像採用の保存が途中です。先に既存の履歴・復旧画面で再開してください。')
            if st.button('画像採用の復旧画面へ'):
                st.session_state['image_ai_'+project.id+'_view'] = '履歴・復旧'
                navigate('イラスト制作','AI画像生成・採用')
            return
        history = service.store.stories.load_history()
        if not history:
            st.info('正式脚本が未登録です。コマを特定するため、先に脚本を保存・確定してください。')
            if st.button('正式脚本の登録へ'):
                navigate('企画・ストーリー','生成・取り込み')
            return
        latest = history[-1]
        art = service.artwork.load()
        jump = st.session_state.pop(prefix+'_jump',None)
        if jump:
            st.session_state[prefix+'_kind'] = 'panel'
            st.session_state[prefix+'_target'] = f'{jump[0]}:{jump[1]}'
        kind = st.radio('手動生成の対象',('panel','character'),format_func=lambda k:'コマ画像' if k == 'panel' else '人物の基準画像',key=prefix+'_kind')
        selection, dirty, material_token, row, following = {}, False, None, None, None
        step_area = st.empty()
        if kind == 'panel':
            from manga.production_dashboard import dashboard
            from manga.direction_drafts_ui import batch_directions
            result = dashboard(project,directory)
            rows = result['panels']
            unfinished_only = st.checkbox('未完成のコマだけを表示',value=True,key=prefix+'_unfinished_only')
            choices = [p for p in rows if not unfinished_only or not p['selected'] or p['stale'] or not p['prompt_ready']]
            # 最後のコマ採用直後も作業位置と成功結果を保持する。
            selected = st.session_state.get(prefix+'_target')
            if selected in [p['key'] for p in rows] and selected not in [p['key'] for p in choices]:
                choices = [p for p in rows if p in choices or p['key']==selected]
            if not choices:
                st.success('全コマに採用画像があります。内容と公開品質は品質チェックで確認してください。')
                if st.button('漫画ページ編集へ'):
                    navigate('漫画ページ編集')
                return
            keys = [p['key'] for p in choices]
            if selected not in keys:
                st.session_state[prefix+'_target'] = keys[0]
            selected = st.selectbox('画像を作るページ・コマ',keys,key=prefix+'_target')
            page,panel = [int(v) for v in selected.split(':')]
            selection = dict(page=page,panel=panel)
            row = next(r for r in rows if r['key']==selected)
            following = _progress(rows,selected,prefix)
            _next(following,selected,prefix)
            batch_directions(service.artwork,prefix,lambda f,m:_run(f,m,**selection))
            st.subheader(f'{page}ページ・{panel}コマ')
            st.subheader('STEP 1：コマの内容を確認')
            source_panel = latest['story']['pages'][page-1]['panels'][panel-1]
            st.write(source_panel['description'])
            st.write('セリフ：'+(source_panel['dialogue'] or 'なし'))
            st.caption('ナレーション：'+(source_panel['narration'] or 'なし'))
            from manga.ux import details
            with details('このコマの脚本版'):
                st.caption('脚本版：'+latest['id'])
            dirty = _inline_direction(service,art,latest,source_panel,row,prefix)
            with st.expander('既存のコマ制作ワークスペース・詳細編集'):
                if st.button('このコマの作画指示を編集'):
                    from manga.panel_workspace_ui import open_panel
                    open_panel(project.id,page,panel,navigate)
            st.subheader('STEP 3：画像生成プロンプト・基準画像・対象確認票を取得')
            material_token = _requirements(service,selection,navigate,result)
            if dirty:
                st.warning('未保存の入力があります。新しい指示を使うため、先にSTEP 2で保存してください。保存まで取り込み・確認・採用を停止します。')
        else:
            people = {c['id']:c for c in art['characters'] if not c['deleted_at']}
            if not people:
                st.info('人物設定が未登録です。基準画像の対象を特定するため、先に人物とバイブルを登録してください。')
                if st.button('人物登録へ'):
                    navigate('キャラクター・作風','手動設定')
                return
            selection['character_id'] = st.selectbox('基準画像を作る人物UUID',list(people),format_func=lambda i:people[i]['name'],key=prefix+'_person')
        identity = digest(dict(kind=kind,selection=selection,version=latest['id']))
        run = lambda function,message:_run(function,message,**selection if kind=='panel' else {})
        if st.button('この対象のプロンプトと基準画像を準備',type='primary',disabled=kind=='panel' and (material_token is None or dirty)):
            def build():
                st.session_state[prefix+'_plan'] = dict(identity=identity,plan=service.plan(kind,**selection))
            run(build,'準備ができました。プロンプト・必要な基準画像・対象確認票を取得してください。')
        saved = st.session_state.get(prefix+'_plan')
        plan = saved['plan'] if saved and saved['identity']==identity else None
        plan_stale = False
        if plan:
            from manga.image_ai_planning import current
            try:
                current(directory,project.id,plan)
            except (ValueError, OSError, StorageError) as error:
                plan_stale = True
                from manga.manual_image_steps import guidance
                st.warning(guidance(error,**selection if kind=='panel' else {}))
                with st.expander('準備済み素材の更新理由'):
                    st.write(str(error))
        if row:
            from manga.manual_image_steps import step_states
            finalized = next(r for r in result['rows'] if r['key']=='finalized')['state']=='完了'
            steps = step_states(row,data,material_token,finalized=finalized,prepared=bool(plan and not plan_stale and not dirty),dirty=dirty)
            with step_area.container():
                for column, half in zip(st.columns(2),(steps[:4],steps[4:])):
                    with column:
                        for step in half:
                            st.markdown(f"**STEP {step['number']}：{step['title']}**　{step['state']}")
                st.caption('「完了」は保存・検証状態です。読了・ダウンロード・外部サービスでの作業や画像の品質を自動認定しません。STEP 4は保存済みの手動画像を根拠に表示します。')
                next_step = next((s for s in steps if s['state']!='完了'),None)
                if next_step:
                    st.info(f"次にやること：STEP {next_step['number']}「{next_step['title']}」")
        if plan and not plan_stale and not dirty:
            _package(service,plan,prefix,identity,art,run)
        elif kind=='panel':
            st.info('STEP 4・5：最新の保存済み指示でSTEP 3の準備ができると、外部制作の案内と取り込み欄を表示します。')
        st.subheader('STEP 6・7：この対象の候補を比較・確認し、別操作で正式採用')
        if not dirty:
            jobs = {j['id']:j for j in data['jobs'] if j['plan']['kind']==kind and all(j['plan'][k]==v for k,v in selection.items())}
            subset = dict(data,candidates=[c for c in data['candidates'] if c['job_id'] in jobs])
            if subset['candidates']:
                candidates(service,subset,prefix+'_'+identity,navigate)
            else:
                st.info('画像候補は未登録です。STEP 3で準備し、外部で制作した画像と確認票をSTEP 5で取り込むと比較・確認できます。')
        if row:
            _next(following,selected,prefix,bottom=True)
        if st.button('画像の履歴・保存復旧を開く'):
            st.session_state['image_ai_'+project.id+'_view'] = '履歴・復旧'
            navigate('イラスト制作','AI画像生成・採用')
    except (ValueError,OSError,StorageError) as error:
        from manga.manual_image_steps import guidance
        from manga.ux import details
        st.error(guidance(error))
        with details('エラーの詳細・復旧情報'):
            st.write(str(error))
