"""画像候補と保存ジャーナル。既存作品データは書かず、履歴を削除しない。"""
import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from uuid import uuid4

from manga.image_ai_models import empty, validate
from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.story_storage import StoryStore


def record(**values):
    return dict(id=str(uuid4()),saved_at=now_iso(),**values)


class ImageAIStore:
    def __init__(self,directory,project_id):
        self.project_id=validate_id(project_id); self.root=Path(directory)/project_id
        self.path=self.root/'image_ai.json'; self.stories=StoryStore(directory,project_id)

    def safe(self,relative):
        raw=PurePosixPath(relative)
        if raw.is_absolute() or not raw.parts or raw.parts[0]!='image_ai' or '..' in raw.parts or '\\' in relative: raise StorageError('画像制作の相対パスが不正です。')
        path=self.root.joinpath(*raw.parts)
        for part in (self.root,path,*path.parents):
            if part==self.root.parent: break
            if part.is_symlink(): raise StorageError('画像制作のリンクを拒否しました。')
        return path

    def sources_safe(self):
        for name in ('image_ai.json','story_history.json','artwork.json','character_ai.json','assistant.json','assistant_pending.json','editorial.json','composition.json','.project.lock','.story.lock','.image-ai.lock','.image-ai-live-generation.lock'):
            if (self.root/name).is_symlink(): raise StorageError('画像制作の設定・ロックへのリンクを拒否しました。')
        self.safe('image_ai')
        if (self.root.parent/(self.project_id+'.json')).is_symlink(): raise StorageError('プロジェクト情報へのリンクを拒否しました。')

    @contextmanager
    def locked(self):
        self.sources_safe()
        self.root.mkdir(parents=True,exist_ok=True)
        with (self.root/'.project.lock').open('a') as project_lock:
            fcntl.flock(project_lock,fcntl.LOCK_EX)
            try:
                with self.stories._lock():
                    self.sources_safe()
                    with (self.root/'.image-ai.lock').open('a') as lock:
                        fcntl.flock(lock,fcntl.LOCK_EX)
                        try: yield
                        finally: fcntl.flock(lock,fcntl.LOCK_UN)
            finally: fcntl.flock(project_lock,fcntl.LOCK_UN)

    def load(self):
        try:
            self.sources_safe()
            marker=self.safe('image_ai/initialized.json')
            if marker.exists() and json.loads(self.read('image_ai/initialized.json',1024))!=dict(schema_version=1,project_id=self.project_id,format='manga-image-ai'):
                raise ValueError('初期化記録が一致しません。')
            if not self.path.exists():
                folder=self.safe('image_ai')
                if folder.is_dir() and any(folder.iterdir()): raise ValueError('画像制作履歴があるのにメタデータがありません。')
                return empty(self.project_id)
            if self.path.stat().st_size>32*1024*1024: raise ValueError
            return validate(json.loads(self.path.read_text(encoding='utf-8')),self.project_id)
        except (ValueError,OSError,KeyError,TypeError,AttributeError,RecursionError) as error:
            raise StorageError('image_ai.jsonを読み込めません。初期化・削除せずバックアップを確認してください。') from error

    def pending(self):
        path=self.safe('image_ai/pending_adoption.json')
        if not path.exists(): return None
        if not path.is_file() or path.stat().st_size>64*1024*1024: raise StorageError('画像採用の保存記録が不正です。')
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            if value['project_id']!=self.project_id or value['state'] not in ('pending','done'): raise ValueError
            validate_id(value['id'])
            return value if value['state']=='pending' else None
        except (ValueError,KeyError,TypeError) as error: raise StorageError('画像採用の保存記録を確認してください。上書きしません。') from error

    def checked(self,revision):
        if self.pending(): raise StorageError('画像採用の保存が途中です。先に保存を再開してください。')
        data=self.load()
        if type(revision) is not int or data['revision']!=revision: raise StorageError('画像制作が更新されました。画面を更新して確認してください。')
        return data

    def save(self,data,kind,**details):
        self.sources_safe()
        expected=dict(schema_version=1,project_id=self.project_id,format='manga-image-ai')
        marker=self.safe('image_ai/initialized.json')
        if marker.exists() and json.loads(self.read('image_ai/initialized.json',1024))!=expected: raise StorageError('初期化記録が不正です。')
        data['revision']+=1; data['events'].append(record(kind=kind,**details))
        validate(data,self.project_id)
        if len(json.dumps(data,ensure_ascii=False).encode())>32*1024*1024: raise StorageError('画像制作履歴の容量上限です。履歴を消さず停止しました。')
        write_json_atomic(self.path,data)
        if not marker.exists(): self.write('image_ai/initialized.json',json.dumps(expected,sort_keys=True).encode(),1024)

    def write(self,relative,content,maximum=20*1024*1024):
        if not isinstance(content,bytes) or not 0<len(content)<=maximum: raise ValueError('画像制作ファイルが空か容量超過です。')
        path=self.safe(relative); path.parent.mkdir(parents=True,exist_ok=True); self.safe(relative)
        if path.exists():
            if self.read(relative,maximum)!=content: raise StorageError('異なる候補・応答の上書きを拒否しました。')
            return path
        temporary=None
        try:
            with NamedTemporaryFile(dir=path.parent,delete=False) as stream:
                temporary=Path(stream.name); stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.link(temporary,path,follow_symlinks=False)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
        return path

    def read(self,relative,maximum=20*1024*1024):
        path=self.safe(relative)
        if not path.exists() or not stat.S_ISREG(path.stat().st_mode) or not 0<path.stat().st_size<=maximum: raise StorageError('候補・応答が欠損、通常ファイル以外、または容量超過です。')
        content=path.read_bytes()
        if not 0<len(content)<=maximum: raise StorageError('読込中に画像の容量が変わりました。')
        return content
