"""GitHubの回答JSONを既存の提案・品質レビュー取り込みへ渡す。正式採用は別。"""
from uuid import uuid4
from manga.sync_manifest import decode_json,SyncConflict,json_bytes,sha256
from manga.sync_local import collect_local,fingerprint
from manga.models import now_iso
from manga.assistant_storage import AssistantStore,record
from manga.assistant_models import normalize_payload
from manga.assistant_changes import context_unchanged
from manga.review_storage import ReviewStore
from manga.review_prompts import parse_ai_review
from manga.review_context import load_context
from manga.review_models import validate_review
from manga.assistant_repository import validate_assistant


def fetch_response(service,branch,path,kind,*,expected_revision):
    if kind not in ('assistant','review'):raise ValueError('回答の種類が不正です。')
    commit=service.remote.head(branch)
    if not commit:raise ValueError('対象ブランチがありません。')
    content=service.remote.read(commit,service._remote_path(path));decode_json(content)
    with service.store.locked(production=True):
        data=service.store.checked(expected_revision);local=collect_local(service.directory,service.project_id)
        if 'story.json' not in local:raise ValueError('先に対応するストーリーと生成依頼を作成してください。')
        # 既存依頼のID・版・対象・現在の設定まで検証する。
        prepare_response(service,kind,content)
        from manga.sync_manifest import build_manifest
        files={'story.json':local['story.json'],f'responses/{kind}.json':content}
        version=decode_json(local['story_history.json'])['versions'][-1]['id']
    candidate=service._stage(branch,commit,files,version,expected_revision)
    return candidate


def prepare_response(service,kind,content):
    if kind=='assistant':
        store=AssistantStore(service.directory,service.project_id);data=store.load();raw=decode_json(content)
        request=store.request(data,raw.get('request_id'));store.latest(request['story_version_id'])
        payload=normalize_payload(raw,request)
        if request['kind'] in ('directions','evaluation','improvement'):context_unchanged(request,store.artwork.load())
        if request['kind']=='references' and any(i['confirmation_status']!='要確認' for i in payload['items']):raise ValueError('AIが提案する参考作品は要確認にしてください。')
        if any(p['payload']==payload for p in data['proposals']):raise ValueError('この返答は取り込み済みです。')
        data['proposals'].append(record(request_id=request['id'],payload=payload,original_payload=payload,edits=[],decisions={i['id']:'確認待ち' for i in payload.get('items',[])}))
        data['revision']+=1;validate_assistant(data,service.project_id)
        return 'assistant.json',json_bytes(data)
    store=ReviewStore(service.directory,service.project_id);data=store.load()
    context=load_context(service.directory,service.project_id,cached=False)
    result=parse_ai_review(content.decode('utf-8-sig'),data['ai_requests'],context)
    if any(r['request_id']==result['request_id'] and r['issues']==result['issues'] for r in data['ai_reviews']):raise ValueError('この画像レビューは取り込み済みです。')
    data['ai_reviews'].append(result);data['revision']+=1;validate_review(data,service.project_id)
    return 'review.json',json_bytes(data)


def accept_response(service,candidate_id,*,expected_revision):
    with service.store.locked(production=True):
        data=service.store.checked(expected_revision);candidate=service._candidate(data,candidate_id)
        local=collect_local(service.directory,service.project_id)
        if candidate['repository']!=service.remote.repository or fingerprint(local)!=candidate['local_fingerprint'] or service.remote.head(candidate['source_branch'])!=candidate['source_commit']:raise SyncConflict('取得後にローカルまたはGitHubが変更されました。再取得してください。')
        files=service._files(candidate);names=[n for n in files if n.startswith('responses/')]
        if len(names)!=1:raise ValueError('AI返答専用の同期候補を選んでください。')
        kind=names[0].split('/')[1].split('.')[0]
        name,content=prepare_response(service,kind,files[names[0]])
        candidate['status']='採用済み';candidate['adopted_scopes']=['response']
        service.store.event(data,'response',candidate_id=candidate_id,target=name)
        data['revision']+=1;updates={name:content,'github_sync.json':json_bytes(data)}
        before={n:service.store.target(n).read_bytes() if service.store.target(n).exists() else None for n in updates}
        service.store.commit(before,updates)
