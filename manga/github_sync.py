"""GitHub ⇄ 候補領域 ⇄ 明示採用。既存作品・mainを自動上書きしない。"""
import copy
from pathlib import Path
from uuid import uuid4
from manga.models import now_iso,validate_id
from manga.storage import StorageError
from manga.sync_manifest import (SyncConflict,safe_path,commit_id,json_bytes,decode_json,sha256,build_manifest,validate_manifest,validate_bundle,compare_files,check_limits)
from manga.sync_local import collect_local,fingerprint,manifest_context
from manga.sync_storage import SyncStore
from manga.sync_legacy import legacy_bundle
from manga.sync_adoption import adoption_plan


class SyncService:
    def __init__(self,directory,project_id,remote):
        self.directory=Path(directory);self.project_id=validate_id(project_id);self.remote=remote
        self.store=SyncStore(directory,project_id);self.prefix=f'works/{project_id}/'

    def _remote_path(self,path):
        safe_path(path)
        if not path.startswith(self.prefix):raise ValueError('選択した作品IDのworksフォルダだけを同期できます。')
        return path

    def browse(self,branch):
        commit=self.remote.head(branch)
        if not commit:raise ValueError('GitHubに対象ブランチがありません。')
        return dict(commit=commit,files=self.remote.list_files(commit,self.prefix))

    def _files(self,record,kind='candidates'):
        validate_manifest(record['manifest'],self.project_id)
        area=self.store.area(kind,record['id'])
        files={}
        for entry in record['manifest']['files']:
            path=area/entry['path']
            if not path.resolve().is_relative_to(area.resolve()):raise StorageError('同期候補のファイルが保存領域を外れます。')
            if path.stat().st_size!=entry['size']:raise StorageError('同期候補のファイル容量が変わりました。元データを確認してください。')
            files[entry['path']]=path.read_bytes()
        validate_bundle(record['manifest'],files,self.project_id)
        return files

    def _stage(self,branch,commit,files,version,expected_revision,manifest=None):
        manifest=manifest or build_manifest(self.project_id,files,story_version_id=version,source_commit=commit,**manifest_context(files))
        validate_bundle(manifest,files,self.project_id)
        with self.store.locked(production=True):
            data=self.store.checked(expected_revision);local=collect_local(self.directory,self.project_id)
            binding=data['bindings'].get(f'{self.remote.repository}:{branch}')
            differences=compare_files({},local,files)
            if binding:
                base=next((c for c in data['candidates'] if c['id']==binding['candidate_id']),None)
                if base:
                    remote_base=self._files(base)
                    # 三者の同じ正規形式を比較。採用時のローカルデータを別途保持する。
                    area=self.store.area('candidates',base['id'])/'local_base'
                    local_base={p.relative_to(area).as_posix():p.read_bytes() for p in area.rglob('*') if p.is_file()}
                    differences=compare_files(remote_base,local,files)
                    conflict=[r for r in differences if r['state']=='競合' and local.get(r['path'])!=local_base.get(r['path'])]
                    if conflict:raise SyncConflict('ローカルとGitHubの両方が変更されています。同期を停止しました。差分を確認し、どちらを新しい候補にするか決めてください。',differences)
            record=dict(id=str(uuid4()),saved_at=now_iso(),source_branch=branch,source_commit=commit,repository=self.remote.repository,
                        manifest=manifest,status='確認待ち',adopted_scopes=[],local_fingerprint=fingerprint(local),differences=differences)
            area=self.store.area('candidates',record['id']);area.mkdir(parents=True)
            for name,content in files.items():
                path=area/safe_path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
            (area/'manifest.json').write_bytes(json_bytes(manifest))
            data['candidates'].append(record);self.store.event(data,'fetch',candidate_id=record['id'],source_commit=commit)
            self.store.save(data);return copy.deepcopy(record)

    def fetch_legacy(self,branch,story_path,*,artwork_path=None,images=None,attachments=None,expected_revision):
        commit=self.remote.head(branch)
        if not commit:raise ValueError('対象のGitHubブランチがありません。')
        story=self.remote.read(commit,self._remote_path(story_path))
        art=self.remote.read(commit,self._remote_path(artwork_path)) if artwork_path else None
        targets=[]
        for item in images or []:
            targets.append(dict(item,content=self.remote.read(commit,self._remote_path(item['path']))))
        files,version=legacy_bundle(self.project_id,commit,story,art,targets)
        for index,path in enumerate(attachments or []):
            self._remote_path(path)
            if not path.endswith('.json'):raise ValueError('参考資料はJSONで指定してください。')
            content=self.remote.read(commit,path);decode_json(content)
            files[f'attachments/{index}_{path.split("/")[-1]}']=content
        return self._stage(branch,commit,files,version,expected_revision)

    def fetch_bundle(self,branch,*,expected_revision):
        commit=self.remote.head(branch)
        if not commit:raise ValueError('対象ブランチがありません。')
        pointer=decode_json(self.remote.read(commit,self.prefix+'sync/latest.json'))
        if not isinstance(pointer,dict) or pointer.get('schema_version')!=1 or pointer.get('project_id')!=self.project_id or not isinstance(pointer.get('manifest_path'),str):raise ValueError('同期ポインターの形式・作品IDが不正です。')
        path=self._remote_path(pointer['manifest_path'])
        if not path.endswith('/manifest.json') or not path.startswith(self.prefix+'sync/'):raise ValueError('同期ポインターの形式が不正です。')
        manifest=validate_manifest(decode_json(self.remote.read(commit,path)),self.project_id)
        root=path[:-len('manifest.json')]
        if root!=self.prefix+f'sync/{manifest["snapshot_id"]}/':raise ValueError('スナップショットIDと保存先が一致しません。')
        files={e['path']:self.remote.read(commit,root+e['path']) for e in manifest['files']}
        return self._stage(branch,commit,files,manifest['story_version_id'],expected_revision,manifest)

    def diff(self,candidate_id):
        data=self.store.load();candidate=self._candidate(data,candidate_id)
        files=self._files(candidate);local=collect_local(self.directory,self.project_id)
        return compare_files({},local,files)

    @staticmethod
    def _candidate(data,identifier):
        validate_id(identifier)
        row=next((r for r in data['candidates'] if r['id']==identifier),None)
        if row is None:raise ValueError('同期候補がありません。')
        return row

    def adopt(self,candidate_id,scopes,*,expected_revision,adopt_selections=False):
        with self.store.locked(production=True):
            data=self.store.checked(expected_revision);candidate=self._candidate(data,candidate_id)
            if candidate['repository']!=self.remote.repository:raise SyncConflict('取得元のリポジトリが異なります。')
            if self.remote.head(candidate['source_branch'])!=candidate['source_commit']:raise SyncConflict('候補取得後にGitHubが更新されました。最新を再取得してください。')
            local=collect_local(self.directory,self.project_id)
            if fingerprint(local)!=candidate['local_fingerprint']:raise SyncConflict('候補取得後にローカルが変更されました。差分を確認し、新しい候補を取得してください。',self.diff(candidate_id))
            files=self._files(candidate)
            updates=adoption_plan(self.directory,self.project_id,local,files,candidate['manifest'],scopes,adopt_selections)
            future=dict(local,**updates)
            if 'story_history.json' in future:future['story.json']=json_bytes(decode_json(future['story_history.json'])['versions'][-1]['story'])
            validate_bundle(build_manifest(self.project_id,future,story_version_id=candidate['manifest']['story_version_id'],source_commit=candidate['source_commit'],**manifest_context(future)),future,self.project_id)
            candidate['adopted_scopes']=sorted(set(candidate['adopted_scopes'])|set(scopes))
            candidate['status']='採用済み' if {'story','settings','images'}<=set(candidate['adopted_scopes']) else '一部採用'
            candidate['local_fingerprint']=fingerprint(future)
            data['bindings'][f'{self.remote.repository}:{candidate["source_branch"]}']=dict(candidate_id=candidate_id,source_commit=candidate['source_commit'])
            self.store.event(data,'adopt',candidate_id=candidate_id,scopes=scopes,adopt_selections=adopt_selections,source_commit=candidate['source_commit'])
            data['revision']+=1;updates['github_sync.json']=json_bytes(data)
            base={name:self.store.target(name).read_bytes() if self.store.target(name).exists() else None for name in updates}
            # 比較の基準は採用後の完全な状態。既存の過去候補は削除しない。
            area=self.store.area('candidates',candidate_id)/'local_base'
            for name,content in future.items():
                path=area/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
            self.store.commit(base,updates)
            return copy.deepcopy(candidate)

    def prepare_export(self,branch,*,expected_revision,outputs=None,source_branch='main'):
        self.remote.branch(branch)
        if branch in ('main','master'):raise ValueError('作品保存用の別ブランチを指定してください。mainには書き込みません。')
        target=self.remote.head(branch);parent=target or self.remote.head(source_branch)
        with self.store.locked(production=True):
            data=self.store.checked(expected_revision);files=collect_local(self.directory,self.project_id)
            if 'story.json' not in files:raise ValueError('採用済みのストーリーがありません。')
            local_fingerprint=fingerprint(files)
            for name,content in (outputs or {}).items():
                safe_path(name)
                if '/' in name or not name.endswith(('.png','.pdf')):raise ValueError('完成ファイル名はPNGまたはPDFで指定してください。')
                files['exports/'+name]=content
            files['pdca.json']=json_bytes(dict(schema_version=1,project_id=self.project_id,policy=data['policy'],iterations=data['iterations']))
            version=decode_json(files['story_history.json'])['versions'][-1]['id']
            manifest=build_manifest(self.project_id,files,story_version_id=version,source_commit=parent,**manifest_context(files))
            validate_bundle(manifest,files,self.project_id)
            record=dict(id=str(uuid4()),saved_at=now_iso(),branch=branch,parent=parent,expected_head=target,manifest=manifest,
                        local_fingerprint=local_fingerprint,repository=self.remote.repository,status='確認待ち')
            area=self.store.area('exports',record['id']);area.mkdir(parents=True)
            for name,content in files.items():
                path=area/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
            data['exports'].append(record);self.store.save(data);return copy.deepcopy(record)

    def push(self,plan_id,*,expected_revision):
        with self.store.locked(production=True):
            data=self.store.checked(expected_revision);record=next((r for r in data['exports'] if r['id']==plan_id),None)
            if record is None or record['status']!='確認待ち':raise ValueError('公開待ちの保存計画がありません。')
            if record['repository']!=self.remote.repository or fingerprint(collect_local(self.directory,self.project_id))!=record['local_fingerprint']:raise SyncConflict('公開計画後に保存データが変更されました。差分を確認して計画を作り直してください。')
            files=self._files(record,'exports');manifest=record['manifest'];root=self.prefix+f'sync/{manifest["snapshot_id"]}/'
            current_head=self.remote.head(record['branch'])
            if current_head!=record['expected_head']:
                # push成功後にローカル記録だけ失敗した場合、内容を照合して記録を復旧する。
                recovered=False
                if current_head:
                    try:
                        found=decode_json(self.remote.read(current_head,root+'manifest.json'))
                        recovered=found==manifest and all(self.remote.read(current_head,root+p)==b for p,b in files.items())
                    except (StorageError,ValueError):pass
                if recovered:
                    record.update(status='保存済み',commit=current_head,recovered=True)
                    self.store.event(data,'push_recovered',plan_id=plan_id,commit=current_head);self.store.save(data)
                    return dict(commit=current_head,branch=record['branch'],recovered=True)
                raise SyncConflict('GitHubのコミットが変わりました。公開を停止し、最新の内容を再取得してください。',
                    [dict(path='GitHub commit',state='競合',before=record['expected_head'],after=current_head)])
            upload={root+name:content for name,content in files.items()}
            upload[root+'manifest.json']=json_bytes(manifest)
            upload[self.prefix+'sync/latest.json']=json_bytes(dict(schema_version=1,project_id=self.project_id,manifest_path=root+'manifest.json'))
            commit=self.remote.publish(record['branch'],record['parent'],record['expected_head'],upload,'Save manga studio snapshot '+self.project_id)
            record.update(status='保存済み',commit=commit)
            self.store.event(data,'push',plan_id=plan_id,commit=commit,branch=record['branch']);self.store.save(data)
            return dict(commit=commit,branch=record['branch'])
