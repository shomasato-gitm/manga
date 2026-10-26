"""明示採用の追記トランザクション。複数JSONの保存失敗を再送なしで復旧する。"""
import copy
import json
from uuid import uuid4

from manga.models import now_iso
from manga.storage import StorageError,write_json_atomic,ProjectStore
from manga.story import normalize_story
from manga.editorial_models import normalize_evaluation
from manga.voice_models import digest
from manga.story_ai_storage import validate_ai
from manga.story_ai_results import candidate,evaluation


def adopt(service,candidate_id,*,expected_revision,expected_story_id,confirmed=False):
    if confirmed is not True:raise ValueError('脚本・差分・評価と、既存作画・音声・動画との整合性を確認してください。')
    with service.store.source_lock(),service.store.lock():
        data=service.store.checked(expected_revision)
        if any(j['state'] in ('queued','running') for j in data['jobs']):raise StorageError('AI制作の完了・停止を確認してから採用してください。')
        selected=candidate(data,candidate_id);history=service.stories.load_history()
        if (history[-1]['id'] if history else None)!=expected_story_id:raise StorageError('正式脚本が更新されました。差分を確認し直してください。')
        if ProjectStore(service.directory).load(service.project_id).page_count!=len(selected['story']['pages']):raise ValueError('候補のページ数と作品設定が異なります。プロジェクト管理でページ数を合わせてから採用してください。')
        previous=next((a for a in reversed(data['adoptions']) if a['candidate_id']==candidate_id and a['story_version_id']==expected_story_id),None)
        if previous:return previous
        job=next(j for j in data['jobs'] if j['id']==selected['job_id'])
        if job['provider']!='mock' and service.source(job['settings'])[1]!=job['source_token']:
            raise StorageError('候補生成後に作品・人物設定・バイブルが変わりました。差分を確認し、新しい条件で候補を作成してください。')
        now=now_iso();identifier=str(uuid4());story_id=str(uuid4())
        version=dict(id=story_id,saved_at=now,action='取り込み',restored_from=None,story=normalize_story(selected['story']).story)
        editorial=service.editorial.load();after_editorial=copy.deepcopy(editorial)
        source_eval=evaluation(data,candidate_id);evaluation_id=None
        if source_eval:
            payload=copy.deepcopy(source_eval['payload']);payload['story_version_id']=story_id
            payload=normalize_evaluation(payload,story_id,version['story']);evaluation_id=str(uuid4())
            after_editorial['evaluations'].append(dict(id=evaluation_id,saved_at=now,payload=payload,source_story=copy.deepcopy(version['story'])))
            after_editorial['revision']+=1;service.editorial.validate(after_editorial)
        adoption=dict(id=identifier,candidate_id=candidate_id,story_version_id=story_id,evaluation_id=evaluation_id,
            source_ai_evaluation_id=source_eval['id'] if source_eval else None,concept_id=selected['concept_id'],created_at=now)
        after_ai=copy.deepcopy(data);after_ai['adoptions'].append(adoption);after_ai['revision']+=1
        after_ai['events'].append(dict(id=str(uuid4()),created_at=now,kind='adopted',adoption_id=identifier));validate_ai(after_ai,service.project_id)
        story_before=dict(schema_version=1,project_id=service.project_id,versions=history) if service.stories.path.exists() else None
        targets=[dict(path='story_history.json',before=story_before,after=dict(schema_version=1,project_id=service.project_id,versions=history+[version]))]
        if source_eval:targets.append(dict(path='editorial.json',before=editorial if service.editorial.path.exists() else None,after=after_editorial))
        targets.append(dict(path='story_ai.json',before=data,after=after_ai))
        journal=dict(id=identifier,project_id=service.project_id,state='pending',targets=targets,adoption=adoption)
        if len(json.dumps(journal,ensure_ascii=False).encode())>48*1024*1024:raise StorageError('採用保存の履歴容量上限です。削除せず、候補JSONを取得して従来の取り込み画面で確認してください。')
        path=service.store.safe('story_ai/adoption_pending.json');path.parent.mkdir(parents=True,exist_ok=True)
        write_json_atomic(path,journal)
        return _finish(service,journal)


def _validate(service,target):
    value=target['after']
    if target['path']=='story_ai.json':validate_ai(value,service.project_id)
    elif target['path']=='editorial.json':service.editorial.validate(value)
    elif target['path']=='story_history.json':
        if value['schema_version']!=1 or value['project_id']!=service.project_id or not value['versions']:raise ValueError('採用脚本の形式が不正です。')
        seen=set()
        for row in value['versions']:
            from manga.models import validate_id
            validate_id(row['id'])
            if row['id'] in seen or normalize_story(row['story']).story!=row['story']:raise ValueError('採用脚本のID・内容が不正です。')
            seen.add(row['id'])
    else:raise ValueError('採用保存の対象が不正です。')


def _finish(service,journal):
    if journal['project_id']!=service.project_id or journal['state'] not in ('pending','done'):raise StorageError('採用保存記録の形式が不正です。')
    if journal['state']=='done':
        if not any(a==journal['adoption'] for a in service.store.load()['adoptions']):raise StorageError('完了した採用の対応記録が見つかりません。')
        archive=service.store.safe(f"story_ai/adoptions/{journal['id']}.json");archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():write_json_atomic(archive,journal)
        return journal['adoption']
    paths=[t['path'] for t in journal['targets']]
    if set(paths) not in ({'story_history.json','story_ai.json'},{'story_history.json','editorial.json','story_ai.json'}) or len(paths)!=len(set(paths)):raise StorageError('採用保存の対象ファイルが不正です。')
    _append_only(journal)
    # Check every target before writing any: independent later edits stop recovery.
    for target in journal['targets']:
        _validate(service,target);path=service.store.root/target['path']
        if path.is_symlink():raise StorageError('正式保存先のリンクを拒否しました。')
        current=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if digest(current) not in (digest(target['before']),digest(target['after'])):raise StorageError('採用保存後に別の編集があります。自動復旧を停止しました。バックアップと差分を確認してください。')
    for target in journal['targets']:
        path=service.store.root/target['path']
        current=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if digest(current)!=digest(target['after']):write_json_atomic(path,target['after'])
    journal=copy.deepcopy(journal);journal['state']='done'
    write_json_atomic(service.store.safe('story_ai/adoption_pending.json'),journal)
    archive=service.store.safe(f"story_ai/adoptions/{journal['id']}.json");archive.parent.mkdir(parents=True,exist_ok=True)
    write_json_atomic(archive,journal)
    return journal['adoption']


def _append_only(journal):
    """壊れた再開記録でも、既存版・参考作品・候補の削除や改変を許可しない。"""
    by_path={t['path']:t for t in journal['targets']};ai=by_path['story_ai.json'];before,after=ai['before'],ai['after'];adoption=journal['adoption']
    try:
        if {k:v for k,v in before.items() if k not in ('revision','adoptions','events')}!={k:v for k,v in after.items() if k not in ('revision','adoptions','events')}:raise ValueError
        if after['revision']!=before['revision']+1 or after['adoptions']!=before['adoptions']+[adoption] or after['events'][:-1]!=before['events'] or after['events'][-1]['adoption_id']!=adoption['id']:raise ValueError
        selected=next(c for c in before['candidates'] if c['id']==adoption['candidate_id'])
        story=by_path['story_history.json'];old_versions=story['before']['versions'] if story['before'] else [];versions=story['after']['versions']
        if len(versions)!=len(old_versions)+1 or versions[:-1]!=old_versions or versions[-1]['id']!=adoption['story_version_id'] or versions[-1]['story']!=selected['story']:raise ValueError
        row=versions[-1]
        from datetime import datetime
        if row['action']!='取り込み' or row['restored_from'] is not None or datetime.fromisoformat(row['saved_at']).tzinfo is None:raise ValueError
        if adoption['evaluation_id'] is None:
            if 'editorial.json' in by_path:raise ValueError
        else:
            target=by_path['editorial.json'];old=target['before'] or dict(schema_version=1,project_id=journal['project_id'],revision=0,references=[],analyses=[],evaluations=[],finalizations=[]);new=target['after']
            if {k:v for k,v in old.items() if k not in ('revision','evaluations')}!={k:v for k,v in new.items() if k not in ('revision','evaluations')}:raise ValueError
            if new['revision']!=old['revision']+1 or new['evaluations'][:-1]!=old['evaluations']:raise ValueError
            evaluation=new['evaluations'][-1];source=next(e for e in before['evaluations'] if e['id']==adoption['source_ai_evaluation_id'])
            payload=copy.deepcopy(source['payload']);payload['story_version_id']=adoption['story_version_id']
            if evaluation['id']!=adoption['evaluation_id'] or evaluation['payload']!=payload or evaluation['source_story']!=selected['story']:raise ValueError
    except (KeyError,TypeError,ValueError,IndexError,StopIteration) as error:raise StorageError('採用再開記録が追記条件に一致しません。既存履歴を変更せず停止しました。') from error


def recover_adoption(service):
    with service.store.source_lock(),service.store.lock():
        path=service.store.safe('story_ai/adoption_pending.json')
        if not path.is_file() or path.stat().st_size>48*1024*1024:raise StorageError('採用の保存記録が見つからないか容量超過です。')
        return _finish(service,json.loads(path.read_text(encoding='utf-8')))
