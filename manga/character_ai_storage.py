"""バイブル・場面例外・検査履歴を原子的に追記する。人物の正本はArtworkStore。"""
import copy
import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from manga.assistant_storage import AssistantStore
from manga.character_ai_models import empty, validate
from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic


def record(**values):
    return dict(id=str(uuid4()), saved_at=now_iso(), **values)


class CharacterAIStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.root = Path(directory) / project_id
        self.path = self.root / 'character_ai.json'
        self.assistant = AssistantStore(directory, project_id)

    def safe_sources(self):
        names = ('character_ai.json', 'assistant.json', 'assistant_pending.json', 'artwork.json', 'editorial.json', 'story_history.json',
                 'character_ai', 'character_ai/initialized.json', '.story.lock', '.artwork.lock', '.editorial.lock', '.assistant.lock', '.character-ai.lock')
        if self.root.is_symlink() or any((self.root/name).is_symlink() for name in names):
            raise StorageError('人物制作の保存先・ロックのリンクを拒否しました。')
        if not self.assistant.path.exists() and self.assistant.journal_path.exists():
            raise StorageError('採用記録があるのに人物提案履歴が欠損しています。初期化せずバックアップを確認してください。')
        if not self.assistant.artwork.path.exists():
            known_characters = (self.root/'images').exists()
            for source, collection in ((self.assistant.path, 'requests'), (self.path, 'bibles')):
                if not source.exists(): continue
                if source.stat().st_size > 16*1024*1024: raise StorageError('作画設定が欠損しています。履歴を初期化せず確認してください。')
                raw = json.loads(source.read_text(encoding='utf-8'))
                known_characters |= bool(raw.get('bibles')) if collection == 'bibles' else any(r.get('context', {}).get('artwork', {}).get('characters') for r in raw.get('requests', []))
            if known_characters:
                raise StorageError('画像・人物の履歴があるのにartwork.jsonが欠損しています。空の人物一覧で上書きせずバックアップを確認してください。')

    @contextmanager
    def locked(self):
        self.safe_sources()
        with self.assistant.locked():
            self.safe_sources()
            with (self.root/'.character-ai.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try: yield
                finally: fcntl.flock(lock, fcntl.LOCK_UN)

    def load(self):
        try:
            self.safe_sources()
            self.marker()
            if not self.path.exists():
                folder = self.root/'character_ai'
                if folder.is_symlink() or folder.is_dir() and any(folder.iterdir()):
                    raise ValueError('人物制作履歴があるのにメタデータがありません。')
                return empty(self.project_id)
            if self.path.stat().st_size > 16*1024*1024: raise ValueError
            return validate(json.loads(self.path.read_text(encoding='utf-8')), self.project_id)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as error:
            raise StorageError('character_ai.jsonを安全に読み込めません。初期化せずバックアップを確認してください。') from error

    def checked(self, revision):
        self.assistant.checked(self.assistant.load()['revision'])
        data = self.load()
        if type(revision) is not int or revision != data['revision']:
            raise StorageError('バイブル・検査が更新されました。画面を更新して確認してください。')
        return data

    def save(self, data, kind, **details):
        self.safe_sources()
        marker = self.marker()
        data['revision'] += 1
        data['events'].append(record(kind=kind, **copy.deepcopy(details)))
        validate(data, self.project_id)
        if len(json.dumps(data, ensure_ascii=False).encode()) > 16*1024*1024:
            raise StorageError('人物制作履歴の容量上限です。既存履歴を削除せず保存を停止しました。')
        write_json_atomic(self.path, data)
        if not marker.exists():
            marker.parent.mkdir(exist_ok=True)
            self.safe_sources()
            write_json_atomic(marker, dict(schema_version=1, format='manga-character-ai', project_id=self.project_id))

    def marker(self):
        marker = self.root/'character_ai'/'initialized.json'
        expected = dict(schema_version=1, format='manga-character-ai', project_id=self.project_id)
        if marker.exists() and (marker.stat().st_size > 1024 or json.loads(marker.read_text(encoding='utf-8')) != expected):
            raise StorageError('人物制作の初期化記録が不正です。バックアップを確認してください。')
        return marker
