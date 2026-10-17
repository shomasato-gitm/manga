"""同期候補・公開計画・PDCA・採用途中の記録。既存データとは別に保存する。"""
import fcntl
import json
import os
from tempfile import NamedTemporaryFile
from contextlib import contextmanager,ExitStack
from pathlib import Path
from uuid import uuid4
from manga.models import validate_id,now_iso
from manga.storage import StorageError,write_json_atomic
from manga.sync_manifest import safe_path,sha256,json_bytes,validate_manifest,commit_id,SyncConflict


def empty_sync(project_id):
    return dict(schema_version=1,project_id=project_id,revision=0,candidates=[],exports=[],events=[],bindings={},
                policy=dict(max_iterations=3,minimum_score=80),iterations=[])


class SyncStore:
    def __init__(self,directory,project_id):
        self.directory=Path(directory);self.project_id=validate_id(project_id);self.root=self.directory/project_id
        self.path=self.root/'github_sync.json';self.journal=self.root/'github_sync_pending.json'

    @contextmanager
    def locked(self,production=False):
        self.root.mkdir(parents=True,exist_ok=True)
        with ExitStack() as stack:
            # AssistantRepositoryと同順。既存のストーリー・作画・ページ保存を横断して保護する。
            names=['project','story','artwork','editorial','assistant','composition','review','studio','sync_outputs','github_sync'] if production else ['github_sync']
            for name in names:
                file=stack.enter_context((self.root/f'.{name}.lock').open('a'))
                fcntl.flock(file,fcntl.LOCK_EX);stack.callback(fcntl.flock,file,fcntl.LOCK_UN)
            yield

    def load(self):
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if type(data['schema_version']) is not int or data['schema_version']!=1 or data['project_id']!=self.project_id or type(data['revision']) is not int or data['revision']<0:raise ValueError
            seen=set()
            for name in ('candidates','exports','events','iterations'):
                if not isinstance(data[name],list):raise ValueError
                for row in data[name]:
                    validate_id(row['id'])
                    if row['id'] in seen:raise ValueError
                    seen.add(row['id'])
            for row in data['exports']:
                validate_manifest(row['manifest'],self.project_id)
                commit_id(row['parent'],True);commit_id(row['expected_head'],True)
                if row['status'] not in ('確認待ち','保存済み'):raise ValueError
                if row['status']=='保存済み':commit_id(row['commit'])
            for row in data['candidates']:
                validate_manifest(row['manifest'],self.project_id);commit_id(row['source_commit'])
                if row['status'] not in ('確認待ち','一部採用','採用済み'):raise ValueError
            policy=data['policy']
            if type(policy['max_iterations']) is not int or not 1<=policy['max_iterations']<=100 or type(policy['minimum_score']) is not int or not 0<=policy['minimum_score']<=100:raise ValueError
            if not isinstance(data['bindings'],dict):raise ValueError
            return data
        except FileNotFoundError:return empty_sync(self.project_id)
        except (OSError,ValueError,TypeError,KeyError) as error:raise StorageError('同期履歴を読めません。github_sync.jsonを上書きせずバックアップを確認してください。') from error

    def checked(self,expected_revision,allow_pending=False):
        data=self.load()
        if type(expected_revision) is not int or data['revision']!=expected_revision:raise SyncConflict('同期履歴が更新されました。画面を更新し、差分を確認してください。')
        if not allow_pending and self.pending():raise SyncConflict('採用保存が途中です。新しい操作の前に「採用保存を再開」を実行してください。')
        if (self.root/'assistant_pending.json').exists():
            from manga.assistant_repository import AssistantRepository
            if AssistantRepository(self.directory,self.project_id).pending():raise SyncConflict('制作提案の採用保存が途中です。先に既存画面で再開してください。')
        return data

    def save(self,data):
        data['revision']+=1
        write_json_atomic(self.path,data)

    @staticmethod
    def event(data,kind,**fields):
        data['events'].append(dict(id=str(uuid4()),saved_at=now_iso(),kind=kind,**fields))

    def area(self,kind,identifier):
        validate_id(identifier)
        if kind not in ('candidates','exports','transactions'):raise ValueError('同期保存領域が不正です。')
        path=self.root/'github_sync'/kind/identifier
        if not path.resolve().is_relative_to(self.root.resolve()):raise StorageError('同期保存領域が外部を参照しています。')
        return path

    def pending(self):
        if not self.journal.exists():return None
        try:
            data=json.loads(self.journal.read_text())
            if data['project_id']!=self.project_id or data['status'] not in ('pending','done'):raise ValueError
            validate_id(data['id'])
            if data['status']=='done':return None
            for item in data['files']:
                safe_path(item['name'])
                if item['name'] not in ('project.json','github_sync.json') and not (item['name'].endswith('.json') and '/' not in item['name']) and not item['name'].startswith(('images/','sync_outputs/')):raise ValueError
                if item['after_hash']!=sha256(self.transaction_file(data['id'],item['name']).read_bytes()):raise ValueError
            return data if data['status']=='pending' else None
        except (OSError,ValueError,KeyError,TypeError) as error:raise StorageError('同期の採用途中記録が不正です。上書きせずバックアップを確認してください。') from error

    def target(self,name):
        safe_path(name)
        path=self.directory/f'{self.project_id}.json' if name=='project.json' else self.root/name
        if not path.resolve().is_relative_to(self.directory.resolve()):raise StorageError('保存先が領域を外れます。')
        if name!='project.json' and not path.resolve().is_relative_to(self.root.resolve()):raise StorageError('保存先が別の作品領域を参照しています。上書きせず停止しました。')
        return path

    def transaction_file(self,identifier,name):
        return self.area('transactions',identifier)/'after'/safe_path(name)

    def commit(self,before,after):
        identifier=str(uuid4());area=self.area('transactions',identifier)
        entries=[]
        for name,content in after.items():
            target=self.target(name);old=target.read_bytes() if target.exists() else None
            expected=before.get(name)
            if old!=expected:raise SyncConflict('採用中に保存データが変わりました。上書きせず停止しました。')
            for folder,value in [('before',old),('after',content)]:
                if value is not None:
                    path=area/folder/name;path.parent.mkdir(parents=True,exist_ok=True)
                    with path.open('wb') as file:
                        file.write(value);file.flush();__import__('os').fsync(file.fileno())
            entries.append(dict(name=name,before_hash=sha256(old) if old is not None else None,after_hash=sha256(content)))
        journal=dict(id=identifier,project_id=self.project_id,status='pending',saved_at=now_iso(),files=entries)
        write_json_atomic(self.journal,journal)
        self.finish(journal)

    def finish(self,journal):
        # 全ファイルを先に照合し、予期しない編集が1件でもあれば停止する。
        for entry in journal['files']:
            target=self.target(entry['name']);current=sha256(target.read_bytes()) if target.exists() else None
            if current not in (entry['before_hash'],entry['after_hash']):raise SyncConflict('採用途中に別の編集がありました。バックアップと差分を確認してください。')
        # 画像を完成させてから、それを参照するJSONと同期完了記録を確定する。
        ordered=sorted(journal['files'],key=lambda e:(e['name']=='github_sync.json',e['name'].endswith('.json')))
        for entry in ordered:
            target=self.target(entry['name']);content=self.transaction_file(journal['id'],entry['name']).read_bytes()
            if target.exists() and sha256(target.read_bytes())==entry['after_hash']:continue
            target.parent.mkdir(parents=True,exist_ok=True)
            if entry['name'].endswith('.json') and entry['before_hash'] is not None:write_json_atomic(target,json.loads(content))
            else:
                # 完成した一時ファイルを排他的にリンクする。容量不足でも途中の画像を残さない。
                temporary=None
                try:
                    with NamedTemporaryFile(dir=target.parent,delete=False,suffix='.tmp') as file:
                        temporary=Path(file.name);file.write(content);file.flush();os.fsync(file.fileno())
                    try:os.link(temporary,target)
                    except FileExistsError as error:
                        raise SyncConflict('保存先に別のファイルが作成されました。上書きせず停止しました。差分を確認してください。') from error
                finally:
                    if temporary is not None:temporary.unlink(missing_ok=True)
        write_json_atomic(self.journal,dict(journal,status='done'))

    def resume(self):
        with self.locked(production=True):
            pending=self.pending()
            if pending:self.finish(pending)
