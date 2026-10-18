"""明示採用する変更だけを作る。人物ID・基準画像・過去候補を保持する。"""
import copy
from manga.models import Project,now_iso
from manga.sync_manifest import decode_json,json_bytes,sha256,SyncConflict
from manga.artwork_models import empty_artwork,character_fields,direction_fields
from manga.page_storage import PageStore

SCOPES={'story':'ストーリー','settings':'人物・作風・作画指示','images':'画像を候補登録','composition':'ページ編集','concepts':'概要案・選択・制作PDCA（参考分析・評価を履歴追加）'}


def image_identity(image):
    """再取得日時だけ違う同一候補は重複登録しない。既存の監査日時は保持する。"""
    value=copy.deepcopy(image);value.pop('saved_at',None)
    context=value.get('context')
    if context:
        if context['direction']:context['direction'].pop('updated_at',None)
        for c in context['characters']:
            c.pop('created_at',None);c.pop('updated_at',None)
    return value


def _merge_images(old,incoming,files,current_files):
    by_id={i['id']:i for i in old}
    for image in incoming:
        if image['id'] in by_id and image_identity(by_id[image['id']])!=image_identity(image):raise SyncConflict('同じ画像IDの設定が異なります。画像IDを勝手に再利用しないでください。')
        path=image['path']
        if path in current_files and current_files[path]!=files[path]:raise SyncConflict('同じ画像IDの画像ハッシュが異なります。基準画像・元画像の上書きを停止しました。')
        if image['id'] not in by_id:old.append(copy.deepcopy(image));by_id[image['id']]=image


def adoption_plan(directory,project_id,current,files,manifest,scopes,adopt_selections=False):
    if not scopes or not set(scopes)<=set(SCOPES):raise ValueError('採用する内容を選択してください。')
    incoming={p:decode_json(b) for p,b in files.items() if p.endswith('.json')}
    result={};version=manifest['story_version_id'];story=incoming.get('story.json')
    history=decode_json(current['story_history.json']) if 'story_history.json' in current else dict(schema_version=1,project_id=project_id,versions=[])
    latest=history['versions'][-1] if history['versions'] else None
    if 'story' in scopes:
        if story is None:raise ValueError('この候補は概要のみです。概要・制作PDCAを選択してください。')
        known=next((v for v in history['versions'] if v['id']==version),None)
        if known and known['story']!=story:raise SyncConflict('同じストーリー版IDに異なる脚本があります。')
        if latest is None or latest['id']!=version:
            if known:raise SyncConflict('過去の版を同期で最新にしません。既存のストーリー履歴画面で復元してください。')
            # 既存履歴を保持し、リモート版IDで新しい取り込みを追加する。
            if 'story_history.json' in incoming:
                known_versions={v['id']:v for v in history['versions']}
                for remote_version in incoming['story_history.json']['versions']:
                    old=known_versions.get(remote_version['id'])
                    if old and old['story']!=remote_version['story']:raise SyncConflict('履歴の版IDに異なる本文があります。')
                    if old is None:history['versions'].append(copy.deepcopy(remote_version))
            else:
                history['versions'].append(dict(id=version,saved_at=now_iso(),action='取り込み',restored_from=None,story=copy.deepcopy(story)))
            result['story_history.json']=json_bytes(history)
        latest=history['versions'][-1]
    concepts_only=manifest.get('content_kind')=='concepts'
    needs_story='composition' in scopes or (any(s in scopes for s in ('settings','images')) and not concepts_only)
    if needs_story and (not latest or latest['id']!=version or latest['story']!=story):raise SyncConflict('作画・画像・ページ編集の脚本版が異なります。対応するストーリーも確認して採用してください。')
    if concepts_only and incoming.get('artwork.json',{}).get('panels'):raise ValueError('概要のみの同期では、脚本に紐づくコマ画像・作画指示を採用できません。')
    if 'project.json' not in current:
        if 'story' not in scopes and not ('concepts' in scopes and 'project.json' in incoming):raise ValueError('初回はストーリーまたは概要・制作PDCAを選び、作品を明示的に登録してください。')
        project=Project.from_dict(incoming['project.json']) if 'project.json' in incoming else Project(id=project_id,title=story['title'],genre='未設定',synopsis=story['summary'] or 'GitHubから取り込んだ作品',page_count=len(story['pages']))
        result['project.json']=json_bytes(project.to_dict())
    if 'settings' in scopes or 'images' in scopes:
        if 'artwork.json' not in incoming:raise ValueError('人物・画像の作画データがありません。')
        art=decode_json(current['artwork.json']) if 'artwork.json' in current else empty_artwork(project_id)
        source=incoming['artwork.json'];by_id={c['id']:c for c in art['characters']}
        for char in source['characters']:
            existing=by_id.get(char['id'])
            if existing is None:
                if 'settings' not in scopes:raise SyncConflict('未登録の人物がいます。人物設定も確認してください。')
                if any(c['name']==char['name'] and not c['deleted_at'] for c in art['characters']):raise SyncConflict('同名の人物に異なるIDがあります。重複候補を既存の人物画面で確認してください。')
                existing=copy.deepcopy(char);existing['references']=[];existing['primary_reference_id']=None
                art['characters'].append(existing);by_id[char['id']]=existing
            if existing['primary_reference_id'] and char['primary_reference_id'] and existing['primary_reference_id']!=char['primary_reference_id']:raise SyncConflict('固定した基準画像IDが異なります。自動で基準画像を差し替えません。')
            if 'settings' in scopes:
                if character_fields(existing)!=character_fields(char):existing.update(character_fields(char),updated_at=now_iso())
                # 削除済み人物を同期だけで復活させない。
                if existing['deleted_at'] and not char['deleted_at']:raise SyncConflict('削除済み人物の復活は同期から行いません。')
            _merge_images(existing['references'],char['references'],files,current)
            if existing['primary_reference_id'] is None:existing['primary_reference_id']=char['primary_reference_id']
        if 'settings' in scopes:art['style']=source['style']
        for key,entry in source['panels'].items():
            target=art['panels'].setdefault(key,dict(page=entry['page'],panel=entry['panel'],direction=None,candidates=[],selected_image_id=None,selection_history=[]))
            if 'settings' in scopes:
                old_direction=copy.deepcopy(target['direction']);new_direction=copy.deepcopy(entry['direction'])
                if old_direction:old_direction.pop('updated_at',None)
                if new_direction:new_direction.pop('updated_at',None)
                if old_direction!=new_direction:target['direction']=copy.deepcopy(entry['direction'])
            if 'images' in scopes:
                _merge_images(target['candidates'],entry['candidates'],files,current)
                if adopt_selections and entry['selected_image_id'] and target['selected_image_id']!=entry['selected_image_id']:
                    target['selected_image_id']=entry['selected_image_id'];target['selection_history'].append(dict(image_id=entry['selected_image_id'],selected_at=now_iso()))
        art['revision']+=1;result['artwork.json']=json_bytes(art)
        for c in art['characters']:
            for image in c['references']:
                if image['path'] not in current:result[image['path']]=files[image['path']]
        if 'images' in scopes:
            for e in source['panels'].values():
                for image in e['candidates']:
                    if image['path'] not in current:result[image['path']]=files[image['path']]
    if 'composition' in scopes:
        if 'composition.json' not in incoming:raise ValueError('同期するページ編集データがありません。')
        data=decode_json(current['composition.json']) if 'composition.json' in current else PageStore(directory,project_id).load()
        source=incoming['composition.json']
        if data['settings']!=source['settings']:
            data['history'].append(dict(kind='settings',saved_at=now_iso(),before=copy.deepcopy(data['settings'])));data['settings']=copy.deepcopy(source['settings'])
        for key,page in source['pages'].items():
            if page['source_version_id']!=version:raise SyncConflict('古い脚本に対するページ編集は自動採用しません。')
            if data['pages'].get(key)!=page:
                data['history'].append(dict(kind='page',page=int(key),saved_at=now_iso(),before=copy.deepcopy(data['pages'].get(key))));data['pages'][key]=copy.deepcopy(page)
        data['revision']+=1;result['composition.json']=json_bytes(data)
    if 'concepts' in scopes:
        from manga.sync_concepts import concept_adoption
        result.update(concept_adoption(directory,project_id,current,incoming,history['versions']))
    return result
