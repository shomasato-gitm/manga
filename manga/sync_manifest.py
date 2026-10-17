"""同期用の外側の契約。既存story.jsonのschema_version: 1は変更しない。"""
import hashlib
import json
import re
from pathlib import PurePosixPath
from uuid import uuid4
from manga.models import now_iso, validate_id
from manga.story import _unique_keys, _invalid_constant

MAX_FILE_BYTES=10*1024*1024
MAX_TOTAL_BYTES=100*1024*1024
MAX_FILES=300
MAX_JSON_BYTES=8*1024*1024


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')


def decode_json(content):
    if len(content)>MAX_JSON_BYTES:raise ValueError('JSONは1ファイル8MiB以内にしてください。履歴は削除せず、同期対象を見直してください。')
    try:return json.loads(content.decode('utf-8-sig'),object_pairs_hook=_unique_keys,parse_constant=_invalid_constant)
    except (UnicodeError,RecursionError,json.JSONDecodeError) as error:raise ValueError('同期JSONをUTF-8の正しい形式にしてください。') from error


def safe_path(path):
    if not isinstance(path,str) or not path or len(path)>1000 or '\\' in path or any(ord(c)<32 for c in path):raise ValueError('同期ファイルのパスが不正です。')
    p=PurePosixPath(path)
    if p.is_absolute() or any(x in ('','.','..') for x in path.split('/')) or '.git' in p.parts:raise ValueError('保存領域を外れるパスは使えません。')
    return path


def commit_id(value,optional=False):
    if value is None and optional:return
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{40}',value):raise ValueError('同期元コミットIDが不正です。')


def check_limits(files):
    if not files or len(files)>MAX_FILES:raise ValueError('同期は1〜300ファイルまでです。')
    for path,content in files.items():
        safe_path(path)
        if not isinstance(content,bytes) or len(content)>MAX_FILE_BYTES:raise ValueError('ファイルは1件10MiB以内にしてください。')
    if sum(map(len,files.values()))>MAX_TOTAL_BYTES:raise ValueError('1回の同期は100MiB以内にしてください。')


def build_manifest(project_id,files,*,story_version_id,source_commit,settings_versions=None,characters=None,adoptions=None):
    validate_id(project_id);validate_id(story_version_id);commit_id(source_commit,True);check_limits(files)
    if settings_versions is None or characters is None or adoptions is None:
        from manga.sync_local import manifest_context
        context=manifest_context(files)
        if settings_versions is None:settings_versions=context['settings_versions']
        if characters is None:characters=context['characters']
        if adoptions is None:adoptions=context['adoptions']
    return dict(schema_version=1,format='manga-studio-sync',project_id=project_id,snapshot_id=str(uuid4()),created_at=now_iso(),
        story_version_id=story_version_id,source_commit=source_commit,settings_versions=settings_versions or {},
        characters=characters or [],adoptions=adoptions or [],
        files=[dict(path=path,size=len(content),sha256=sha256(content)) for path,content in sorted(files.items())])


def validate_manifest(manifest,project_id):
    try:
        if type(manifest['schema_version']) is not int or manifest['schema_version']!=1 or manifest['format']!='manga-studio-sync' or manifest['project_id']!=validate_id(project_id):raise ValueError('同期manifestの作品ID・形式が一致しません。')
        validate_id(manifest['snapshot_id']);validate_id(manifest['story_version_id']);commit_id(manifest['source_commit'],True)
        entries=manifest['files']
        if not isinstance(entries,list) or not 1<=len(entries)<=MAX_FILES:raise ValueError('同期ファイル数が不正です。')
        seen=set();total=0
        for entry in entries:
            path=safe_path(entry['path'])
            if path in seen:raise ValueError('同期パスが重複しています。')
            seen.add(path)
            if type(entry['size']) is not int or not 0<=entry['size']<=MAX_FILE_BYTES or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):raise ValueError('ファイルの容量・ハッシュが不正です。')
            total+=entry['size']
        if total>MAX_TOTAL_BYTES:raise ValueError('同期合計が100MiBを超えます。')
        if not isinstance(manifest['settings_versions'],dict) or not isinstance(manifest['characters'],list) or not isinstance(manifest['adoptions'],list):raise ValueError('版・人物・採用状態が不正です。')
        if any(type(v) is not int or v<0 for v in manifest['settings_versions'].values()):raise ValueError('設定版は0以上の整数にしてください。')
    except (KeyError,TypeError,AttributeError) as error:raise ValueError('同期manifestに必要な項目がありません。') from error
    return manifest


def validate_bundle(manifest,files,project_id):
    validate_manifest(manifest,project_id);check_limits(files)
    if set(files)!={e['path'] for e in manifest['files']}:raise ValueError('manifestと取得ファイル一覧が一致しません。')
    for entry in manifest['files']:
        content=files[entry['path']]
        if len(content)!=entry['size'] or sha256(content)!=entry['sha256']:raise ValueError(f'{entry["path"]}の容量・ハッシュが一致しません。同期を停止しました。')
    from manga.sync_validation import validate_payload
    payload=validate_payload(files,project_id,manifest)
    from manga.sync_local import manifest_context
    expected=manifest_context(files)
    if any(manifest[k]!=expected[k] for k in expected):raise ValueError('人物ID・基準画像ハッシュ・版・採用状態がmanifestと一致しません。')
    return payload


def json_changes(before,after,path=''):
    if isinstance(before,dict) and isinstance(after,dict):
        return [change for key in sorted(set(before)|set(after)) for change in json_changes(before.get(key),after.get(key),f'{path}.{key}'.strip('.'))]
    if isinstance(before,list) and isinstance(after,list):
        return [change for i in range(max(len(before),len(after))) for change in json_changes(before[i] if i<len(before) else None,after[i] if i<len(after) else None,f'{path}[{i}]')]
    return [] if before==after else [dict(path=path,before=before,after=after)]


def compare_files(base,local,remote):
    rows=[]
    for path in sorted(set(base)|set(local)|set(remote)):
        b,l,r=base.get(path),local.get(path),remote.get(path)
        state='同一' if l==r else '競合' if l!=b and r!=b else 'ローカル変更' if l!=b else 'GitHub変更'
        changes=[]
        if path.endswith('.json') and l is not None and r is not None:
            changes=json_changes(decode_json(l),decode_json(r))
        rows.append(dict(path=path,state=state,local_hash=sha256(l) if l is not None else None,remote_hash=sha256(r) if r is not None else None,changes=changes))
    return rows


class SyncConflict(ValueError):
    """差分の確認が必要。自動マージ・強制書き込みはしない。"""
    def __init__(self,message,differences=None):
        super().__init__(message)
        self.differences=differences or []
