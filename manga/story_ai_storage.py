"""確定データとは別の候補保存。版ロック・追記履歴・応答の原子的保存。"""
import copy
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path,PurePosixPath
from tempfile import NamedTemporaryFile
from uuid import uuid4

from manga.models import validate_id,now_iso
from manga.storage import StorageError,write_json_atomic
from manga.voice_storage import VoiceStore

STATES=('queued','running','completed','stopped','paused','failed')
REQUEST_STATES=('queued','sending','settled','applied','uncertain','not_sent','invalid')


def empty(identifier):
    return dict(schema_version=1,format='manga-story-ai',project_id=identifier,revision=0,settings=None,card=None,
                concept_batches=[],selections=[],candidates=[],evaluations=[],jobs=[],requests=[],adoptions=[],events=[])


def validate_ai(raw,project_id):
    if set(raw)!=set(empty(project_id)) or type(raw['schema_version']) is not int or raw['schema_version']!=1 or raw['format']!='manga-story-ai' or raw['project_id']!=project_id or type(raw['revision']) is not int or raw['revision']<0:raise ValueError
    for key in ('concept_batches','selections','candidates','evaluations','jobs','requests','adoptions','events'):
        if not isinstance(raw[key],list):raise ValueError
        identifiers=[validate_id(i['id']) for i in raw[key]]
        if len(identifiers)!=len(set(identifiers)):raise ValueError
    if any(j['state'] not in STATES for j in raw['jobs']) or any(r['state'] not in REQUEST_STATES or r['request_id']!=r['id'] for r in raw['requests']):raise ValueError
    from manga.story_ai_models import settings
    if raw['settings'] is not None and settings(raw['settings'])!=raw['settings']:raise ValueError
    idea_ids=[]
    for batch in raw['concept_batches']:idea_ids.extend(validate_id(i['id']) for i in batch['ideas'])
    if len(set(idea_ids))!=len(idea_ids):raise ValueError
    if any(s['concept_id'] not in idea_ids for s in raw['selections']):raise ValueError
    jobs={j['id']:j for j in raw['jobs']}
    for job in jobs.values():
        if settings(job['settings'])!=job['settings'] or type(job['cycle']) is not int or not 0<=job['cycle']<=5 or job['kind'] not in ('concepts','story') or job['provider'] not in ('mock','openai-responses','gemini-text'):raise ValueError
    for request in raw['requests']:
        validate_id(request['result_id'])
        if request['job_id'] not in jobs or type(request['cycle']) is not int or not 0<=request['cycle']<=5 or request['kind'] not in ('concepts','draft','evaluation','improve'):raise ValueError
    from manga.story import normalize_story
    from manga.editorial_models import normalize_evaluation
    candidates={c['id']:c for c in raw['candidates']}
    for c in candidates.values():
        if normalize_story(c['story']).story!=c['story'] or c['concept_id'] not in idea_ids or c['job_id'] not in jobs:raise ValueError
    for e in raw['evaluations']:
        candidate=candidates[e['candidate_id']]
        if normalize_evaluation(e['payload'],candidate['id'],candidate['story'])!=e['payload']:raise ValueError
    return raw


class AIStoryStore:
    def __init__(self,directory,project_id):
        self.project_id=validate_id(project_id);self.root=Path(directory)/project_id;self.path=self.root/'story_ai.json'

    def safe(self,relative):
        raw=PurePosixPath(relative)
        if raw.is_absolute() or not raw.parts or raw.parts[0]!='story_ai' or '..' in raw.parts or '\\' in relative:raise ValueError('AI脚本の保存先が不正です。')
        path=self.root.joinpath(*raw.parts)
        for p in [self.root,path,*path.parents]:
            if p==self.root.parent:break
            if p.is_symlink():raise StorageError('AI脚本保存先のリンクを拒否しました。')
        return path

    @contextmanager
    def lock(self,*,generation=False):
        if self.root.is_symlink() or self.path.is_symlink():raise StorageError('AI脚本データへのリンクを拒否しました。')
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/('.story-ai-generation.lock' if generation else '.story-ai.lock')
        if path.is_symlink():raise StorageError('ロックへのリンクを拒否しました。')
        with path.open('a') as file:
            try:fcntl.flock(file,fcntl.LOCK_EX | (fcntl.LOCK_NB if generation else 0))
            except BlockingIOError:raise StorageError('この作品のAI脚本処理は実行中です。') from None
            try:yield
            finally:fcntl.flock(file,fcntl.LOCK_UN)

    def source_lock(self):return VoiceStore(self.root.parent,self.project_id).locked(sources=True)

    def load(self):
        try:
            if self.root.is_symlink() or self.path.is_symlink():raise ValueError
            if not self.path.exists():
                artifacts=self.safe('story_ai')
                if artifacts.is_dir() and any(artifacts.iterdir()):raise ValueError('応答履歴があるのにメタデータがありません。')
                return empty(self.project_id)
            if self.path.stat().st_size>24*1024*1024:raise ValueError
            return validate_ai(json.loads(self.path.read_text(encoding='utf-8')),self.project_id)
        except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError) as error:raise StorageError('story_ai.jsonを安全に読み込めません。削除・初期化せずバックアップを確認してください。') from error

    def checked(self,revision):
        pending=self.safe('story_ai/adoption_pending.json')
        if pending.is_file() and pending.stat().st_size>48*1024*1024:raise StorageError('採用再開記録の容量が不正です。バックアップを確認してください。')
        if pending.is_file() and json.loads(pending.read_text(encoding='utf-8'))['state']=='pending':raise StorageError('採用の保存が途中です。先に採用保存を再開してください。')
        data=self.load()
        if type(revision) is not int or data['revision']!=revision:raise StorageError('AI脚本設定が更新されました。画面を再読み込みしてください。')
        return data

    def save(self,data,kind,**fields):
        data['revision']+=1;data['events'].append(dict(id=str(uuid4()),created_at=now_iso(),kind=kind,**copy.deepcopy(fields)))
        validate_ai(data,self.project_id)
        if len(json.dumps(data,ensure_ascii=False).encode())>24*1024*1024:raise StorageError('候補履歴の容量上限です。既存履歴を削除せず停止しました。')
        write_json_atomic(self.path,data)

    def write(self,relative,content):
        if not isinstance(content,bytes) or len(content)>2*1024*1024:raise ValueError('AI応答の形式・容量が不正です。')
        path=self.safe(relative);path.parent.mkdir(parents=True,exist_ok=True);self.safe(relative)
        if path.exists():
            if path.stat().st_size!=len(content) or path.read_bytes()!=content:raise StorageError('異なるAI応答の上書きを拒否しました。')
            return path
        temporary=None
        try:
            with NamedTemporaryFile(dir=path.parent,delete=False) as file:
                temporary=Path(file.name);file.write(content);file.flush();os.fsync(file.fileno())
            os.link(temporary,path,follow_symlinks=False)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return path

    def read(self,relative):
        path=self.safe(relative)
        if not path.is_file() or path.stat().st_size>2*1024*1024:raise StorageError('保存済みAI応答が欠損・容量超過です。')
        return path.read_bytes()
