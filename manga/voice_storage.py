"""音声だけの版管理と追記型ファイル保存。既存作品データへ書き込まない。"""
import copy
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager, ExitStack
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from uuid import uuid4

from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.voice_models import empty_voice, voice_settings

STATES = ('queued','running','ready','uncertain','artifact_pending','not_sent','closed')


def validate_voice(data,project_id):
    if data['schema_version']!=1 or type(data['schema_version']) is not int or data['format']!='manga-voice-project' or data['project_id']!=project_id:
        raise ValueError('音声設定の作品ID・形式が不正です。')
    if set(data)!=set(empty_voice(project_id)):raise ValueError('音声設定に未対応の項目があります。')
    for key in ('revision','settings_revision'):
        if type(data[key]) is not int or data[key]<0:raise ValueError('音声設定の版が不正です。')
    for speaker,settings in data['voices'].items():
        validate_id(speaker)
        if voice_settings(settings)!=settings:raise ValueError('声設定が正規形式ではありません。')
    for identifier,edit in data['edits'].items():
        validate_id(identifier);validate_id(edit['speaker_id'])
        if not isinstance(edit['source_text_hash'],str) or len(edit['source_text_hash'])!=64:raise ValueError('読みの生成元が不正です。')
        from manga.voice_models import speech_text, EMOTIONS
        speech_text(edit['spoken_text'])
        if edit['emotion'] not in EMOTIONS:raise ValueError('発話の感情が不正です。')
    for collection in ('jobs','assets','timelines','events'):
        if not isinstance(data[collection],list):raise ValueError('音声履歴は配列にしてください。')
        ids=set()
        for row in data[collection]:
            validate_id(row['id'])
            if row['id'] in ids:raise ValueError('音声履歴のIDが重複しています。')
            ids.add(row['id'])
    jobs={j['id']:j for j in data['jobs']}
    for job in jobs.values():
        if job['request_id']!=job['id'] or job['state'] not in STATES:raise ValueError('音声ジョブの状態が不正です。')
        validate_id(job['asset_id']);validate_id(job['story_version_id'])
        if voice_settings(job['settings'])!=job['settings']:raise ValueError('音声ジョブの声設定が不正です。')
        if job['settings']['provider']!='mock':
            from manga.voice_pricing import price_quote
            price_quote(job['pricing'],job['settings']['engine'])
        from manga.voice_models import cache_key
        if job['cache_key']!=cache_key(job['utterance'],job['settings']):raise ValueError('音声ジョブの生成元とハッシュが一致しません。')
    assets={a['id']:a for a in data['assets']}
    for asset in assets.values():
        if asset['path']!=f"voice/audio/{asset['id']}.wav" or asset['job_id'] not in jobs:
            raise ValueError('音声素材の保存先・生成元が不正です。')
        if len(asset['cache_key'])!=64 or len(asset['info']['sha256'])!=64:
            raise ValueError('音声素材のハッシュが不正です。')
        source=jobs[asset['job_id']]
        if asset['id']!=source['asset_id'] or asset['cache_key']!=source['cache_key'] or asset['settings']!=source['settings'] or asset['speaker_id']!=source['utterance']['speaker_id']:
            raise ValueError('音声素材と生成ジョブの人物・設定が一致しません。')
    for identifier,asset_id in data['assignments'].items():
        validate_id(identifier)
        if asset_id not in assets:raise ValueError('採用する音声が履歴にありません。')
    if data['pricing'] is not None and not isinstance(data['pricing'],dict):raise ValueError('料金設定が不正です。')
    if data['pricing'] is not None:
        from manga.voice_pricing import price_quote
        for engine in data['pricing']['engines']:price_quote(data['pricing'],engine)
    return data


class VoiceStore:
    def __init__(self,directory,project_id):
        self.project_id=validate_id(project_id)
        self.root=Path(directory)/project_id
        self.path=self.root/'voice.json'

    def safe_path(self,relative):
        raw=PurePosixPath(relative)
        if raw.is_absolute() or '..' in raw.parts or not raw.parts or raw.parts[0]!='voice' or '\\' in relative:
            raise ValueError('音声ファイルの保存先が不正です。')
        path=self.root.joinpath(*raw.parts)
        for parent in (self.root,*path.parents):
            if parent==self.root.parent:break
            if parent.is_symlink():raise ValueError('音声保存先にシンボリックリンクは使えません。')
        if path.is_symlink():raise ValueError('音声ファイルへのリンクは使用できません。')
        return path

    @contextmanager
    def locked(self,*,generation=False,sources=False):
        if self.root.is_symlink() or self.path.is_symlink():raise StorageError('音声設定へのリンクは使用できません。')
        self.root.mkdir(parents=True,exist_ok=True)
        names=['voice-generation'] if generation else ['project','story','artwork','editorial','voice'] if sources else ['voice']
        with ExitStack() as stack:
            for name in names:
                path=self.root/f'.{name}.lock'
                if path.is_symlink():raise StorageError('音声ロックへのリンクを拒否しました。')
                lock=stack.enter_context(path.open('a'))
                fcntl.flock(lock,fcntl.LOCK_EX);stack.callback(fcntl.flock,lock,fcntl.LOCK_UN)
            yield

    def load(self):
        try:
            if self.root.is_symlink() or self.path.is_symlink():raise ValueError('リンクです。')
            if self.path.stat().st_size>16*1024*1024:raise ValueError('音声履歴は16MiBまでです。')
            return validate_voice(json.loads(self.path.read_text(encoding='utf-8')),self.project_id)
        except FileNotFoundError:return empty_voice(self.project_id)
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:
            raise StorageError('音声履歴を安全に読み込めません。削除・上書きせずバックアップを確認してください。') from error

    def checked(self,revision):
        data=self.load()
        if type(revision) is not int or data['revision']!=revision:
            raise StorageError('別の操作で音声設定が更新されました。画面を更新して確認してください。')
        return data

    def check_storage(self):
        directory=self.safe_path('voice/staging')
        directory.mkdir(parents=True,exist_ok=True)
        self.safe_path('voice/staging')
        temporary=None;linked=directory/f'.probe-{uuid4()}'
        try:
            with NamedTemporaryFile(dir=directory,prefix='.probe-',delete=False) as file:
                temporary=Path(file.name);file.write(b'probe');file.flush();os.fsync(file.fileno())
            os.link(temporary,linked,follow_symlinks=False)
        except OSError as error:
            raise StorageError('音声保存先に書き込み・ハードリンクが必要です。API送信前に停止しました。') from error
        finally:
            linked.unlink(missing_ok=True)
            if temporary is not None:temporary.unlink(missing_ok=True)

    def save(self,data,kind,*,before=None,**fields):
        data['events'].append(dict(id=str(uuid4()),saved_at=now_iso(),kind=kind,before=copy.deepcopy(before),**fields))
        data['revision']+=1
        validate_voice(data,self.project_id)
        if len(json.dumps(data,ensure_ascii=False).encode())>16*1024*1024:
            raise StorageError('音声履歴の上限です。履歴を削除せず保存を停止しました。')
        write_json_atomic(self.path,data)

    def write_blob(self,relative,content):
        if not isinstance(content,bytes) or len(content)>25*1024*1024:raise ValueError('音声成果物の形式・容量が不正です。')
        path=self.safe_path(relative)
        path.parent.mkdir(parents=True,exist_ok=True)
        self.safe_path(relative)
        temporary=None
        try:
            if path.exists():
                if not path.is_file() or path.stat().st_size!=len(content) or path.read_bytes()!=content:raise StorageError('同名の異なる音声成果物があります。上書きを停止しました。')
                return path
            with NamedTemporaryFile(dir=path.parent,prefix='.voice-',delete=False) as file:
                temporary=Path(file.name);file.write(content);file.flush();os.fsync(file.fileno())
            try:os.link(temporary,path,follow_symlinks=False)
            except FileExistsError:
                if path.read_bytes()!=content:raise StorageError('音声成果物の保存が競合しました。')
            return path
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)

    def read_blob(self,relative,maximum=25*1024*1024):
        path=self.safe_path(relative)
        if not path.is_file() or path.stat().st_size>maximum:raise ValueError('音声成果物が欠損・容量超過です。')
        return path.read_bytes()

    def job_receipt(self,job):
        path=f"voice/staging/{job['id']}.json"
        receipt=json.loads(self.read_blob(path,64*1024))
        if receipt['job_id']!=job['id'] or receipt['asset_id']!=job['asset_id']:raise ValueError('保存途中の音声とジョブが一致しません。')
        content=self.read_blob(f"voice/staging/{job['id']}.wav")
        if hashlib.sha256(content).hexdigest()!=receipt['info']['sha256']:
            raise ValueError('保存途中の音声が変更されています。再送せず確認してください。')
        return receipt,content
