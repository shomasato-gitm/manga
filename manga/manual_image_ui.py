"""手動生成の対象・プロンプト・基準画像・取り込み・比較を1画面へつなぐ。"""
import hashlib
import json

from manga.ui_runtime import ui as st
from manga.image_ai_service import ImageAIService
from manga.image_ai_ui import action
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


def render_manual_images(project, directory, navigate):
    st.header('手動画像制作')
    st.caption('ChatGPT等で生成するプロンプトと基準画像を取得し、画像を手動で戻します。画像APIへの送信は0回・0円です。')
    service = ImageAIService(directory,project.id)
    prefix = 'manual_'+project.id
    try:
        data = service.store.load()
        from manga.production_dashboard import recovery_routes
        other_recoveries = [r for r in recovery_routes(directory,project.id) if r['section'] not in ('AI画像生成・採用','音声制作・字幕','動画制作・MP4出力')]
        if other_recoveries:
            item = other_recoveries[0]
            st.warning(item['label']+'の保存が途中です。既存の復旧画面で再開してください。')
            if st.button('保存途中の制作画面へ'): navigate(item['menu'],item['section'])
            return
        if service.store.pending():
            st.warning('画像採用の保存が途中です。先に既存の履歴・復旧画面で再開してください。')
            if st.button('画像採用の復旧画面へ'):
                st.session_state['image_ai_'+project.id+'_view'] = '履歴・復旧'
                navigate('イラスト制作','AI画像生成・採用')
            return
        history = service.store.stories.load_history()
        if not history:
            st.info('先に正式脚本を保存・確定してください。')
            return
        latest = history[-1]
        art = service.artwork.load()
        jump = st.session_state.pop(prefix+'_jump',None)
        if jump:
            st.session_state[prefix+'_kind'] = 'panel'
            st.session_state[prefix+'_target'] = f'{jump[0]}:{jump[1]}'
        kind = st.radio('手動生成の対象',('panel','character'),format_func=lambda k:'コマ画像' if k == 'panel' else '人物の基準画像',key=prefix+'_kind')
        selection = {}
        if kind == 'panel':
            from manga.production_dashboard import dashboard
            rows = dashboard(project,directory)['panels']
            unfinished_only = st.checkbox('未完成のコマだけを表示',value=True,key=prefix+'_unfinished_only')
            choices = [p for p in rows if not unfinished_only or not p['selected'] or p['stale'] or not p['prompt_ready']]
            if not choices:
                st.success('全コマに採用画像があります。画像内容は品質チェックで確認してください。')
                if st.button('漫画ページ編集へ'): navigate('漫画ページ編集')
                return
            keys = [p['key'] for p in choices]
            if st.session_state.get(prefix+'_target') not in keys: st.session_state[prefix+'_target'] = keys[0]
            selected = st.selectbox('画像を作るページ・コマ',keys,key=prefix+'_target')
            page,panel = [int(v) for v in selected.split(':')]
            selection = dict(page=page,panel=panel)
            st.caption(f'対象：{page}ページ・{panel}コマ / 脚本版：{latest["id"]}')
            if st.button('次に画像が必要なコマへ',disabled=len(choices)<2):
                following = choices[(keys.index(selected)+1)%len(choices)]
                st.session_state[prefix+'_jump'] = (following['page'],following['panel'])
                st.rerun()
            if st.button('このコマの作画指示を編集'):
                from manga.panel_workspace_ui import open_panel
                open_panel(project.id,page,panel,navigate)
        else:
            people = {c['id']:c for c in art['characters'] if not c['deleted_at']}
            if not people:
                st.info('先に人物設定とバイブルを登録してください。')
                return
            selection['character_id'] = st.selectbox('基準画像を作る人物UUID',list(people),format_func=lambda i:people[i]['name']+' / '+i,key=prefix+'_person')
        identity = digest(dict(kind=kind,selection=selection,version=latest['id']))
        if st.button('この対象のプロンプトと基準画像を準備',type='primary'):
            def build():
                st.session_state[prefix+'_plan'] = dict(identity=identity,plan=service.plan(kind,**selection))
            action(build,'手動生成の準備ができました。プロンプト・基準画像・対象確認票を取得してください。')
        saved = st.session_state.get(prefix+'_plan')
        if saved and saved['identity'] == identity:
            plan = saved['plan']
            from manga.image_ai_planning import current
            current(directory,project.id,plan)
            from manga.prompt_ui import prompt_tools
            prompt_tools(plan['prompt'],prefix+'_'+identity+'_prompt','manual_prompt.txt',response_json=False)
            st.json(receipt(plan),expanded=False)
            st.download_button('対象確認票をダウンロード',json.dumps(receipt(plan),ensure_ascii=False,indent=2).encode(),file_name=f"manual_p{plan['page']}_c{plan['panel']}_{plan['story_version_id']}.json",mime='application/json')
            if plan['reference_images']:
                refs = {r['id']:r for r in plan['reference_images']}
                identifier = st.selectbox('添付する基準画像',list(refs),format_func=lambda i:refs[i]['character_id']+' / '+i,key=prefix+'_ref')
                if st.checkbox('基準画像を確認・ダウンロード',key=prefix+'_show_ref'):
                    from manga.video_sources import file_bytes
                    ref = refs[identifier]
                    content = file_bytes(service.store.root,ref['path'],20*1024*1024)
                    if hashlib.sha256(content).hexdigest() != ref['sha256']: raise ValueError('基準画像が変更されました。準備をやり直してください。')
                    st.image(content,width=280)
                    st.download_button('基準画像PNGをダウンロード',content,file_name=identifier+'.png',mime='image/png')
            st.subheader('生成した画像をこの対象へ取り込む')
            upload = st.file_uploader('手動生成画像',type=['png','jpg','jpeg','webp'],key=prefix+'_'+identity+'_upload')
            manifest = st.file_uploader('生成時の対象確認票（必須）',type=['json'],key=prefix+'_'+identity+'_receipt')
            st.caption('確認票のページ番号・コマ番号・人物UUID・脚本版を照合します。画像の外見と人物の一致は、取り込み後に人間が比較・確認してください。')
            upload_sha = hashlib.sha256(upload.getvalue()).hexdigest() if upload else None
            if upload_sha: st.caption('取り込み対象のSHA-256：'+upload_sha)
            if st.button('検証して画像候補を取り込む',disabled=upload is None or manifest is None):
                def import_image():
                    from manga.automation import Automation
                    Automation(directory,project.id).import_manual(plan,manifest.getvalue(),upload.getvalue(),upload.name,upload_sha)
                action(import_image,'候補を保存しました。外見・権利の確認後に明示採用してください。')
        st.subheader('この対象の候補を比較・正式採用')
        jobs = {j['id']:j for j in data['jobs'] if j['plan']['kind'] == kind and all(j['plan'][k] == v for k,v in selection.items())}
        subset = dict(data,candidates=[c for c in data['candidates'] if c['job_id'] in jobs])
        candidates(service,subset,prefix+'_'+identity,navigate)
        if st.button('画像の履歴・保存復旧を開く'):
            st.session_state['image_ai_'+project.id+'_view'] = '履歴・復旧'
            navigate('イラスト制作','AI画像生成・採用')
    except (ValueError,OSError,StorageError) as error:
        st.error(str(error))
