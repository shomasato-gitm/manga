"""候補は少数のサムネイルから比較し、人間の確認と採用を別操作にする。"""
import hashlib

from manga.gallery_ui import page_items, thumbnail
from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_planning import current
from manga.image_ai_ui import action
from manga.storage import StorageError
from manga.ui_runtime import ui as st


def candidates(service,data,prefix,navigate):
    if not data['candidates']: st.info('候補はまだありません。無料モックまたは手動画像から1候補作成してください。'); return
    jobs={j['id']:j for j in data['jobs']}
    targets={}
    for c in data['candidates']:
        p=jobs[c['job_id']]['plan']; key=p['character_id'] if p['kind']=='character' else f"{p['page']}:{p['panel']}"
        targets.setdefault((p['kind'],key),[]).append(c)
    keys=list(targets)
    selected_target=st.selectbox('比較する画像の対象',keys,format_func=lambda k:('人物 '+k[1]) if k[0]=='character' else k[1]+' ページ:コマ',key=prefix+'_compare_target')
    images=list(reversed(targets[selected_target]))
    for c in page_items(images,prefix+'_candidate_page','候補比較',size=2):
        st.caption(c['id'][:8]+' / '+('AI生成ではないテスト用画像' if c['mock'] else '手動画像・権利は人間確認'))
        st.image(thumbnail(service,c),width=300)
    by_id={c['id']:c for c in images}
    identifier=st.selectbox('確認・採用する画像候補',list(by_id),format_func=lambda i:i[:8]+' / '+('テスト画像' if by_id[i]['mock'] else '手動画像'),key=prefix+'_candidate')
    candidate=by_id[identifier]; job=jobs[candidate['job_id']]; plan=job['plan']
    st.caption(f"実際の画像寸法：{candidate['width']}×{candidate['height']} ／ 計画サイズ：{plan['options']['size']}")
    if f"{candidate['width']}x{candidate['height']}"!=plan['options']['size']: st.warning('手動画像の寸法は計画と異なります。見切れ・縦横比・次の動画工程の画素数上限を確認してください。自動トリミングはしません。')
    stale=False
    try: current(service.directory,service.project_id,plan)
    except (ValueError,OSError,StorageError) as error: stale=True; st.warning('要再確認：'+str(error))
    if candidate['mock']: st.warning('これはAI生成ではないテスト用画像です。人物の再現品質・完成品としての画質は未検証です。')
    if st.checkbox('選択候補の拡大・PNG取得',key=prefix+'_'+identifier+'_full'):
        payload=service.candidate_bytes(candidate); st.image(payload,use_container_width=True)
        st.download_button('選択候補PNGをダウンロード',payload,file_name=identifier+'.png',mime='image/png')
    if candidate['provider']=='manual' and st.checkbox('変換前の手動入力を取得',key=prefix+'_'+identifier+'_original'):
        raw=service.store.read(f"image_ai/jobs/{job['id']}/input.bin")
        if hashlib.sha256(raw).hexdigest()!=job['input']['sha256']: raise StorageError('元入力のSHAが一致しません。')
        st.download_button('元の手動画像をダウンロード',raw,file_name=job['input']['name'])
    with st.expander('元プロンプト・参照画像・SHA・構図の記録'): st.json(dict(candidate=candidate,plan=plan),expanded=False)
    st.subheader('人間による目視・権利確認')
    checks={k:st.checkbox('確認：'+label,key=prefix+'_'+identifier+'_'+k,disabled=stale) for k,label in REVIEW_FIELDS.items()}
    note=st.text_area('目視確認メモ・素材の出典（必須）',max_chars=2000,key=prefix+'_'+identifier+'_review_note',disabled=stale)
    if st.button('この候補の目視・権利確認を保存',disabled=stale or not all(checks.values()) or not note.strip()):
        action(lambda:service.review(identifier,checks,note,expected_revision=data['revision']),'人間の確認を保存しました。次の採用操作で初めて正規画像を変更します。')
    review=next((r for r in reversed(data['reviews']) if r['candidate_id']==identifier and r['sha256']==candidate['sha256'] and r['material_token']==plan['material_token']),None)
    art=service.artwork.load()
    approved=st.checkbox('この候補を正規登録・採用し、前の採用画像も履歴へ保持することを承認した',key=prefix+'_'+identifier+'_'+str(art['revision'])+'_adopt',disabled=stale)
    if st.button('この候補をArtworkへ登録・採用',type='primary',disabled=stale or not approved or review is None):
        action(lambda:service.adopt(identifier,expected_revision=data['revision'],expected_artwork_revision=art['revision'],confirmed=approved),'候補を正規ArtworkStoreへ登録・採用しました。既存画像と履歴は保持しています。基準画像変更後はバイブルを再確認してください。')
    adopted=[a for a in data['adoptions'] if a['candidate_id']==identifier]
    if adopted: st.caption('正規登録済み画像ID：'+adopted[-1]['image_id']+'（現在の採用状態は既存画像管理でも確認できます）')
    if candidate['mock'] and st.button('同じ設定で次のモック候補の依頼を作る'):
        def regenerate():
            selection={k:plan[k] for k in ('character_id','page','panel','view','expression') if plan[k] is not None}
            fresh=service.plan(plan['kind'],options=plan['options'],**selection)
            service.create_job(fresh,new_request=True,expected_revision=service.store.load()['revision'])
        action(regenerate,'次のモック依頼を保存しました。生成・手動取り込みから1候補を作成してください。')
    if navigate:
        if plan['kind']=='character' and st.button('基準画像変更後のバイブル確認へ'):
            st.session_state['character_ai_'+service.project_id+'_view']='キャラクターバイブル'
            st.session_state['character_ai_'+service.project_id+'_bible_person']=plan['character_id']
            navigate('キャラクター・作風','AIキャラクター制作・一貫性管理')
        elif plan['kind']=='panel' and st.button('採用したコマの既存制作画面へ'):
            from manga.panel_workspace_ui import open_panel
            open_panel(service.project_id,plan['page'],plan['panel'],navigate)
        if st.button('音声・字幕工程へ（生成は自動実行しない）'): navigate('完成・書き出し','音声制作・字幕')
