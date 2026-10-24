"""基準画像をID・安全なローカルパス・ハッシュで参照する。外見解析はしない。"""
import hashlib
import stat
from pathlib import Path

from manga.models import validate_id
from manga.storage import StorageError

MAX_REFERENCE_BYTES = 32 * 1024 * 1024


def reference_path(root, image):
    identifier = validate_id(image['id'])
    if image['path'] != f'images/references/{identifier}.png':
        raise StorageError('参照画像IDと保存先が一致しません。')
    root = Path(root)
    path = root / image['path']
    for part in (root, root/'images', root/'images'/'references', path):
        if part.is_symlink():
            raise StorageError('参照画像のシンボリックリンクを拒否しました。')
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise StorageError('参照画像は通常ファイルにしてください。')
    return path


def references(root, character, *, hashed=False):
    result = []
    for image in character['references']:
        path = reference_path(root, image)
        stamp = path.stat() if path.exists() else None
        if stamp and stamp.st_size > MAX_REFERENCE_BYTES:
            raise StorageError('参照画像が32MiBを超えています。既存ファイルを保持して確認してください。')
        sha = None
        if hashed and stamp:
            digest = hashlib.sha256()
            with path.open('rb') as source:
                total = 0
                while chunk := source.read(65536):
                    total += len(chunk)
                    if total > MAX_REFERENCE_BYTES: raise StorageError('参照画像の容量が変わりました。')
                    digest.update(chunk)
            after = path.stat()
            if (stamp.st_mtime_ns, stamp.st_ctime_ns, stamp.st_size, stamp.st_ino) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size, after.st_ino):
                raise StorageError('参照画像が読み込み中に更新されました。再確認してください。')
            sha = digest.hexdigest()
        result.append(dict(id=image['id'], path=image['path'], view=image['view'], note=image['note'], sha256=sha,
                           state='present' if stamp else 'missing', size=stamp.st_size if stamp else None,
                           signature=[stamp.st_dev, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns] if stamp else None))
    return result
