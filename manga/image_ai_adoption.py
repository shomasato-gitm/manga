"""人間の確認後に既存ArtworkStoreメソッドを使う。登録・選択を同一IDで復旧する。"""
import copy
import hashlib
import json
from uuid import uuid4

from manga.artwork_models import validate_artwork
from manga.image_ai_planning import current
from manga.image_ai_storage import record
from manga.images import prepare_image
from manga.models import validate_id
from manga.storage import StorageError, write_json_atomic
from manga.voice_models import digest


def image_marker(registration_id,candidate):
    suffix='TEST_ONLY' if candidate['mock'] else 'API' if candidate['provider']=='gemini-live' else 'MANUAL'
    return 'image-ai-'+registration_id+'-'+suffix+'.png'


def adopt(service,candidate_id,*,expected_revision,expected_artwork_revision,confirmed=False):
    if confirmed is not True: raise ValueError('正規登録・採用と既存画像への影響を確認してください。')
    validate_id(candidate_id)
    with service.store.locked():
        data=service.store.checked(expected_revision)
        candidate=next((c for c in data['candidates'] if c['id']==candidate_id),None)
        if not candidate: raise ValueError('採用候補がありません。')
        prior=next((a for a in data['adoptions'] if a['candidate_id']==candidate_id),None)
        job=next(j for j in data['jobs'] if j['id']==candidate['job_id'])
        current(service.directory,service.project_id,job['plan'],hashed=True)
        content=service.candidate_bytes(candidate)
        review=next((r for r in reversed(data['reviews']) if r['candidate_id']==candidate_id and r['sha256']==candidate['sha256'] and r['material_token']==job['plan']['material_token']),None)
        if not review: raise ValueError('この候補と現在の設定を人間が目視・権利確認してから採用してください。')
        art=service.artwork.load()
        if type(expected_artwork_revision) is not int or art['revision']!=expected_artwork_revision: raise StorageError('作画設定が更新されました。再確認してください。')
        if prior:
            entry,_=_entry(art,job['plan'])
            selected=entry['primary_reference_id'] if job['plan']['kind']=='character' else entry['selected_image_id']
            if selected==prior['image_id']: return prior
        identifier=str(uuid4())
        registration_id=prior['registration_id'] if prior and 'registration_id' in prior else prior['id'] if prior else identifier
        marker=image_marker(registration_id,candidate)
        journal=dict(id=identifier,project_id=service.project_id,state='pending',candidate_id=candidate_id,review_id=review['id'],
            before_artwork=art,marker=marker,canonical_sha256=hashlib.sha256(prepare_image(content)[0]).hexdigest(),image_id=prior['image_id'] if prior else None,
            registration_id=registration_id,mode='reselect' if prior else 'new',stage='register')
        _save_journal(service,journal)
        return finish(service,journal)


def _save_journal(service,journal):
    path=service.store.safe('image_ai/pending_adoption.json'); path.parent.mkdir(parents=True,exist_ok=True)
    if len(json.dumps(journal,ensure_ascii=False).encode())>64*1024*1024: raise StorageError('採用記録の容量上限です。既存画像を保持して停止しました。')
    write_json_atomic(path,journal)


def _entry(art,plan):
    if plan['kind']=='character':
        person=next((c for c in art['characters'] if c['id']==plan['character_id']),None)
        if not person or person['deleted_at']: raise StorageError('登録先の人物が削除されています。')
        return person,person['references']
    entry=art['panels'].get(f"{plan['page']}:{plan['panel']}")
    return entry,entry['candidates'] if entry else []


def _preserved(before,after,plan,image,*,allow_selected,existing=False):
    """採用対象以外の設定・人物・画像・履歴が変わっていないことを先に検証する。"""
    expected=copy.deepcopy(after)
    entry,images=_entry(expected,plan)
    if sum(i['id']==image['id'] for i in images)!=1: raise StorageError('採用する登録画像が重複しています。')
    if not existing: images[:]=[i for i in images if i['id']!=image['id']]
    old_entry,_=_entry(before,plan)
    if plan['kind']=='character':
        if entry['primary_reference_id']!=old_entry['primary_reference_id'] and entry['primary_reference_id']!=image['id']: raise StorageError('基準画像の選択が別編集で変わっています。')
        entry['primary_reference_id']=old_entry['primary_reference_id']
    else:
        original_selection=old_entry['selected_image_id'] if old_entry else None
        original_history=old_entry['selection_history'] if old_entry else []
        if entry['selected_image_id']!=original_selection:
            if not allow_selected or entry['selected_image_id']!=image['id'] or len(entry['selection_history'])!=len(original_history)+1 or entry['selection_history'][:-1]!=original_history or entry['selection_history'][-1]['image_id']!=image['id']:
                raise StorageError('画像選択が別編集で変わっています。')
        elif entry['selection_history']!=original_history: raise StorageError('画像選択履歴が変わっています。')
        entry['selected_image_id']=original_selection; entry['selection_history']=copy.deepcopy(original_history)
        if old_entry is None:
            if entry['direction'] is not None or images: raise StorageError('新しいコマへ別の編集があります。')
            expected['panels'].pop(f"{plan['page']}:{plan['panel']}")
    expected['revision']=before['revision']
    if expected!=before: raise StorageError('保存途中に別の編集があります。自動上書きを停止しました。差分とバックアップを確認してください。')


def finish(service,journal):
    data=service.store.load()
    already=next((a for a in data['adoptions'] if a['id']==journal['id']),None)
    if already:
        _save_journal(service,dict(journal,state='done')); return already
    candidate=next(c for c in data['candidates'] if c['id']==journal['candidate_id'])
    job=next(j for j in data['jobs'] if j['id']==candidate['job_id']); plan=job['plan']
    before=journal['before_artwork']; validate_artwork(before,service.project_id)
    review=next((r for r in data['reviews'] if r['id']==journal['review_id'] and r['candidate_id']==candidate['id'] and r['sha256']==candidate['sha256']),None)
    if not review or review['material_token']!=plan['material_token']: raise StorageError('人間の確認記録が一致しません。')
    content=service.candidate_bytes(candidate)
    canonical=hashlib.sha256(prepare_image(content)[0]).hexdigest()
    mode=journal['mode']
    if mode not in ('new','reselect'): raise StorageError('採用保存の操作が不正です。')
    registration_id=validate_id(journal['registration_id'])
    if mode=='new' and registration_id!=journal['id']: raise StorageError('登録操作IDが一致しません。')
    if mode=='reselect' and not any(a['id']==registration_id and a['candidate_id']==candidate['id'] and a['image_id']==journal['image_id'] for a in data['adoptions']): raise StorageError('既存画像の採用元が見つかりません。')
    expected_marker=image_marker(registration_id,candidate)
    if journal['project_id']!=service.project_id or journal['marker']!=expected_marker or journal['canonical_sha256']!=canonical: raise StorageError('画像採用の再開記録が一致しません。')
    art=service.artwork.load(); entry,images=_entry(art,plan)
    registered=[i for i in images if i['original_name']==journal['marker']]
    if len(registered)>1: raise StorageError('採用IDの登録が重複しています。')
    if not registered:
        if mode=='reselect': raise StorageError('正規登録された画像メタデータが欠損しています。再発行せず停止しました。')
        if digest(art)!=digest(before): raise StorageError('登録前の作画設定に別の編集があります。復旧を停止しました。')
        current(service.directory,service.project_id,plan,hashed=True)
        if plan['kind']=='character':
            image=service.artwork.add_reference(plan['character_id'],content,journal['marker'],plan['view'],note='image_ai候補 '+candidate['id']+' / '+review['rights_status'],expected_revision=art['revision'])
        else:
            image=service.artwork.add_panel_image(plan['page'],plan['panel'],content,journal['marker'],story_version_id=plan['story_version_id'],expected_revision=art['revision'])
        art=service.artwork.load()
    else:
        image=registered[0]
    if journal['image_id'] is not None and journal['image_id']!=image['id']: raise StorageError('登録済み画像IDが変わっています。再発行しません。')
    _preserved(before,art,plan,image,allow_selected=True,existing=mode=='reselect')
    journal=dict(journal,image_id=image['id'],stage='select'); _save_journal(service,journal)
    path=service.artwork.image_path(image)
    for part in (service.artwork.root,path,*path.parents):
        if part==service.artwork.root.parent: break
        if part.is_symlink(): raise StorageError('正規画像のリンクを拒否しました。')
    if not path.exists():
        service.artwork.restore_registered_image(image,content,canonical,expected_revision=art['revision']); art=service.artwork.load()
    if hashlib.sha256(service.artwork.image_bytes(image)).hexdigest()!=canonical: raise StorageError('正規画像のSHAが違います。上書きせず停止しました。')
    entry,_=_entry(art,plan)
    selected=entry['primary_reference_id'] if plan['kind']=='character' else entry['selected_image_id']
    if selected!=image['id']:
        if plan['kind']=='character': service.artwork.select_reference(plan['character_id'],image['id'],expected_revision=art['revision'])
        else: service.artwork.select_panel_image(plan['page'],plan['panel'],image['id'],expected_revision=art['revision'],story_version_id=plan['story_version_id'])
        art=service.artwork.load()
    _preserved(before,art,plan,image,allow_selected=True,existing=mode=='reselect')
    data=service.store.load()
    row=record(candidate_id=candidate['id'],review_id=review['id'],image_id=image['id'],registered_sha256=canonical,
               kind=plan['kind'],character_id=plan['character_id'],page=plan['page'],panel=plan['panel'],mock=candidate['mock'],story_version_id=plan['story_version_id'],registration_id=registration_id)
    row['id']=journal['id']; data['adoptions'].append(row); service.store.save(data,'image_adopted',adoption_id=row['id'])
    _save_journal(service,dict(journal,state='done',stage='done'))
    return row


def recover(service,*,confirmed=False):
    if confirmed is not True: raise ValueError('同じ画像IDの採用保存を再開することを確認してください。')
    with service.store.locked():
        journal=service.store.pending()
        if not journal: return None
        return finish(service,journal)
