"""video.jsonだけを版管理する。成果物は追記し、既存素材を変更しない。"""
import copy
import fcntl
import json
import re
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from manga.models import validate_id,now_iso
from manga.storage import StorageError,write_json_atomic
from manga.voice_storage import VoiceStore


class VideoStore:
    def __init__(self,directory,project_id):
        self.project_id=validate_id(project_id);self.root=Path(directory)/project_id;self.path=self.root/'video.json'

    def safe(self,relative):
        raw=PurePosixPath(relative)
        if raw.is_absolute() or not raw.parts or raw.parts[0]!='video' or '..' in raw.parts or '\\' in relative:raise ValueError('動画の保存先が不正です。')
        path=self.root.joinpath(*raw.parts)
        for item in [self.root,path,*path.parents]:
            if item==self.root.parent:break
            if item.is_symlink():raise StorageError('動画保存先のリンクを拒否しました。')
        return path

    @contextmanager
    def lock(self,generation=False):
        if self.root.is_symlink() or self.path.is_symlink():raise StorageError('動画データへのリンクは使用できません。')
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/('.video-render.lock' if generation else '.video.lock')
        if path.is_symlink():raise StorageError('動画ロックのリンクを拒否しました。')
        with path.open('a') as file:
            try:fcntl.flock(file,fcntl.LOCK_EX | (fcntl.LOCK_NB if generation else 0))
            except BlockingIOError:raise StorageError('この作品は別の動画ジョブを実行中です。状態を更新してください。') from None
            try:yield
            finally:fcntl.flock(file,fcntl.LOCK_UN)

    def load(self):
        if self.root.is_symlink():raise StorageError('作品フォルダへのリンクを拒否しました。')
        if not self.path.exists() and not self.path.is_symlink():
            return dict(schema_version=1,format='manga-video-project',project_id=self.project_id,revision=0,plans={},plan_history=[],audio=[],jobs=[],exports=[],events=[])
        try:
            if self.root.is_symlink() or self.path.is_symlink() or self.path.stat().st_size>16*1024*1024:raise ValueError
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if type(data['schema_version']) is not int or data['schema_version']!=1 or data['format']!='manga-video-project' or data['project_id']!=self.project_id or type(data['revision']) is not int or data['revision']<0:raise ValueError
            if not isinstance(data['plans'],dict):raise ValueError
            for key in ('plan_history','audio','jobs','exports','events'):
                if not isinstance(data[key],list):raise ValueError
                ids=[validate_id(r['id']) for r in data[key]]
                if len(set(ids))!=len(ids):raise ValueError
            for item in data['audio']:
                if item['extension'] not in ('wav','mp3') or item['path']!=f"video/audio/{item['id']}.{item['extension']}" or type(item['rights_reviewed']) is not bool or not re.fullmatch('[0-9a-f]{64}',item['sha256']):raise ValueError
            for item in data['jobs']:
                validate_id(item['timeline_id'])
                if item['state'] not in ('queued','running','ready','failed','cancelled','interrupted') or not re.fullmatch('[0-9a-f]{64}',item['fingerprint']):raise ValueError
            for item in data['exports']:
                validate_id(item['job_id'])
                if item['kind'] not in ('youtube_main','tiktok_vertical','youtube_shorts') or item['path']!=f"video/exports/{item['job_id']}/{item['kind']}.mp4" or not re.fullmatch('[0-9a-f]{64}',item['sha256']):raise ValueError
            for event in data['events']:
                if event['kind'] in ('production_validation','production_final_review'):
                    if not re.fullmatch('[0-9a-f]{64}',event['token']):raise ValueError
                if event['kind'] == 'production_validation':
                    for key in ('audio_done','audio_total','audio_reviewed'):
                        if type(event[key]) is not int or not 0 <= event[key] <= 10000:raise ValueError
                    if not 0 <= event['audio_reviewed'] <= event['audio_done'] <= event['audio_total'] or type(event['mock_audio']) is not bool:raise ValueError
                    if event['timeline_id'] is not None:validate_id(event['timeline_id'])
                    if not isinstance(event['export_ids'],list) or len(set(event['export_ids'])) != len(event['export_ids']) or len(event['export_ids']) > 3:raise ValueError
                    for identifier in event['export_ids']:validate_id(identifier)
                    for key in ('errors','warnings'):
                        if not isinstance(event[key],list) or any(not isinstance(v,str) for v in event[key]):raise ValueError
                if event['kind'] == 'production_final_review':
                    if not isinstance(event['checks'],dict) or not event['checks'] or any(type(v) is not bool for v in event['checks'].values()) or not isinstance(event['note'],str) or not event['note'].strip():raise ValueError
            return data
        except (OSError,ValueError,KeyError,TypeError,RecursionError) as error:
            raise StorageError('video.jsonを安全に読み込めません。既存ファイルを削除せずバックアップを確認してください。') from error

    def checked(self,revision):
        data=self.load()
        if type(revision) is not int or data['revision']!=revision:raise StorageError('別の操作で動画設定が更新されました。再読み込みして確認してください。')
        return data

    def save(self,data,kind,**fields):
        data['revision']+=1;data['events'].append(dict(id=str(uuid4()),created_at=now_iso(),kind=kind,**copy.deepcopy(fields)))
        if len(json.dumps(data,ensure_ascii=False).encode())>16*1024*1024:raise StorageError('動画履歴の容量上限です。自動削除せず停止しました。')
        write_json_atomic(self.path,data)

    def immutable(self,relative,content):
        # Reuse immutable, fsync + hard-link writer with a video-only namespace.
        import os
        from tempfile import NamedTemporaryFile
        path=self.safe(relative);path.parent.mkdir(parents=True,exist_ok=True);self.safe(relative)
        if path.exists():
            if path.stat().st_size!=len(content) or path.read_bytes()!=content:raise StorageError('異なる動画成果物の上書きを拒否しました。')
            return path
        temporary=None
        try:
            with NamedTemporaryFile(dir=path.parent,delete=False) as file:
                temporary=Path(file.name);file.write(content);file.flush();os.fsync(file.fileno())
            os.link(temporary,path,follow_symlinks=False)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return path

    def source_lock(self):
        return VoiceStore(self.root.parent,self.project_id).locked(sources=True)
