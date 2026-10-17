"""GitHubとの通信は通常のHTTPS Gitだけ。API・強制push・削除を使わない。"""
import os
import fcntl
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from contextlib import contextmanager
from manga.sync_manifest import safe_path,commit_id,check_limits,MAX_FILE_BYTES
from manga.storage import StorageError


class GitRemote:
    def __init__(self,repository,cache,token=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository) or any(x in ('.','..') for x in repository.split('/')):raise ValueError('GitHubリポジトリはowner/name形式にしてください。')
        self.repository=repository;self.url=f'https://github.com/{repository}.git';self.cache=Path(cache);self.token=token;self.local=False

    @classmethod
    def for_local_test(cls,path,cache):
        obj=object.__new__(cls);obj.repository='local/test';obj.url=str(Path(path).resolve());obj.cache=Path(cache);obj.token=None;obj.local=True
        return obj

    @staticmethod
    def branch(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,149}',value) or any(s in value for s in ('..','//','@{')) or value.endswith(('/','.','.lock')):raise ValueError('ブランチ名が不正です。')
        return value

    @contextmanager
    def _environment(self):
        env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_ALLOW_PROTOCOL='file' if self.local else 'https')
        # Gitのトークンを引数・URL・JSONへ含めない。認証がなければ通常のcredential helperを使う。
        if self.token:
            with tempfile.TemporaryDirectory(prefix='manga-askpass-') as temp:
                script=Path(temp)/'askpass.sh'
                script.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" x-access-token;; *) printf "%s\\n" "$MANGA_GIT_TOKEN";; esac\n');script.chmod(0o700)
                env.update(GIT_ASKPASS=str(script),MANGA_GIT_TOKEN=self.token)
                yield env
        else:
            yield env

    def _run(self,*args,content=None,extra_env=None):
        with self._environment() as env:
            env.update(extra_env or {})
            try:
                # 明示トークンはaskpass限定。既存helperによる認証情報の保存も止める。
                options=['-c','credential.helper='] if self.token else []
                result=subprocess.run(['git','-c','core.hooksPath='+os.devnull,*options,'-C',str(self.cache),*args],input=content,capture_output=True,timeout=120,env=env)
            except (OSError,subprocess.TimeoutExpired) as error:raise StorageError('Gitを実行できないか通信がタイムアウトしました。Gitのインストール・接続を確認してください。') from error
            if result.returncode:
                # stderrには認証ヘルパーの出力が含まれうるためUIや保存ファイルへ流さない。
                raise StorageError('Git通信に失敗しました。認証の権限、ブランチ、通信状態を確認してください。ローカルデータは削除していません。')
            return result.stdout

    def _ready(self):
        if not (self.cache/'HEAD').exists():
            self.cache.mkdir(parents=True,exist_ok=True)
            self._run('init','--bare');self._run('remote','add','origin',self.url)
        elif self._run('remote','get-url','origin').decode().strip()!=self.url:raise StorageError('Gitキャッシュのリポジトリが異なります。別のキャッシュを指定してください。')

    def head(self,branch):
        self.branch(branch);self.cache.mkdir(parents=True,exist_ok=True)
        # FETCH_HEADは共有可変ファイル。同じ作品を別タブで取得しても混線させない。
        with (self.cache/'.fetch.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                self._ready()
                value=self._run('ls-remote','--heads','origin',f'refs/heads/{branch}').decode().strip()
                if not value:return None
                self._run('fetch','--no-tags','--depth=1','origin',f'refs/heads/{branch}')
                result=self._run('rev-parse','FETCH_HEAD').decode().strip();commit_id(result);return result
            finally:fcntl.flock(lock,fcntl.LOCK_UN)

    def list_files(self,commit,prefix):
        commit_id(commit);safe_path(prefix.rstrip('/'))
        result={}
        for record in self._run('ls-tree','-rlz',commit,'--',prefix).split(b'\0'):
            if not record:continue
            header,path=record.split(b'\t',1);mode,kind,oid,size=header.split()
            name=path.decode('utf-8');safe_path(name)
            if kind!=b'blob' or mode not in (b'100644',b'100755'):raise ValueError('同期対象にシンボリックリンク等が含まれています。通常ファイルだけを指定してください。')
            result[name]=int(size)
        return result

    def read(self,commit,path):
        commit_id(commit);safe_path(path)
        if path.isascii():
            args=['ls-tree','-z',commit,'--',path]
        else:
            # 非ASCIIの最初の要素より手前だけをGitの検索条件にする。
            # パス引数を正規化しうるMacでも、ツリーの生の名前を照合できる。
            parts=path.split('/');index=next(i for i,part in enumerate(parts) if not part.isascii())
            prefix='/'.join(parts[:index])
            args=['ls-tree','-rz',commit]+(['--',prefix+'/'] if prefix else [])
        entries=self._run(*args,extra_env={'GIT_LITERAL_PATHSPECS':'1'}).split(b'\0')
        canonical=unicodedata.normalize('NFC',path)
        matches=[entry for entry in entries if entry and unicodedata.normalize('NFC',entry.split(b'\t',1)[1].decode('utf-8'))==canonical]
        if len(matches)>1:raise ValueError('Unicode正規化で同名になるファイルが複数あります。取得先を推測せず停止しました。GitHub上の名前を確認してください。')
        if not matches or matches[0].split(b' ',1)[0] not in (b'100644',b'100755'):
            raise ValueError('取得対象は存在する通常ファイルにしてください。リンク・フォルダは取得できません。')
        oid=matches[0].split(b'\t',1)[0].split()[2].decode('ascii')
        size=int(self._run('cat-file','-s',oid))
        if size>MAX_FILE_BYTES:raise ValueError('取得ファイルが10MiBを超えています。')
        # 検証した通常ファイルのblobを直接読む。ファイル名を再解釈させない。
        return self._run('cat-file','blob',oid)

    def publish(self,branch,parent,expected_head,files,message):
        self.branch(branch);check_limits(files)
        if branch in ('main','master'):raise ValueError('main/masterへの書き込みは行いません。作品同期ブランチを指定してください。')
        current=self.head(branch)
        if current!=expected_head:
            from manga.sync_manifest import SyncConflict
            raise SyncConflict('GitHubが更新されました。再取得して差分を確認してください。')
        if parent:commit_id(parent)
        with tempfile.TemporaryDirectory(prefix='manga-git-index-') as temp:
            env=dict(GIT_INDEX_FILE=str(Path(temp)/'index'),GIT_AUTHOR_NAME='AI Manga Studio',GIT_AUTHOR_EMAIL='studio@local.invalid',GIT_COMMITTER_NAME='AI Manga Studio',GIT_COMMITTER_EMAIL='studio@local.invalid')
            self._run('read-tree',parent if parent else '--empty',extra_env=env)
            for path,content in files.items():
                # 既存ファイルは同一内容の場合だけ許可。更新するのはlatestポインターのみ。
                exists=self._run('ls-tree',parent,'--',path).strip() if parent else b''
                if exists and not path.endswith('/sync/latest.json') and self.read(parent,path)!=content:raise StorageError('GitHubの既存ファイルを上書きする操作を停止しました。新しいスナップショットを作ってください。')
                oid=self._run('hash-object','-w','--stdin',content=content).decode().strip()
                self._run('update-index','--add','--cacheinfo','100644',oid,path,extra_env=env)
            tree=self._run('write-tree',extra_env=env).decode().strip()
            args=['commit-tree',tree]+(['-p',parent] if parent else [])
            commit=self._run(*args,content=(message+'\n').encode(),extra_env=env).decode().strip()
            try:self._run('push','origin',f'{commit}:refs/heads/{branch}')
            except StorageError as error:
                from manga.sync_manifest import SyncConflict
                raise SyncConflict('pushに失敗しました。競合・認証を確認し、再取得してください。強制pushは行いません。') from error
            return commit
