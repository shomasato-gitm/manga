"""表示専用の変更検知キャッシュ。保存処理は従来どおり直接読み込み・検証する。"""

import copy
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from manga.storage import StorageError

MAX_BYTES = 8 * 1024 * 1024
_CACHE = OrderedDict()
_LOCK = RLock()
_STATS = dict(hits=0, misses=0)


def signature(path):
    try:
        stat = Path(path).stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StorageError("保存先の状態を確認できません。権限とフォルダの場所を確認してください。") from error


def cached_read(path, loader):
    path = str(Path(path).resolve())
    stamp = signature(path)
    with _LOCK:
        entry = _CACHE.get(path)
        if entry and entry[0] == stamp:
            _STATS["hits"] += 1
            _CACHE.move_to_end(path)
            return copy.deepcopy(entry[1])
    _STATS["misses"] += 1
    value = loader()
    # 読み込み中に更新された結果を別の版としてキャッシュしない。
    if signature(path) == stamp and (stamp is None or stamp[2] <= MAX_BYTES // 2):
        with _LOCK:
            _CACHE[path] = (stamp, copy.deepcopy(value))
            while sum((item[0][2] if item[0] else 0) + 1024 for item in _CACHE.values()) > MAX_BYTES or len(_CACHE) > 32:
                _CACHE.popitem(last=False)
    return value


def read_store(store):
    loader = getattr(store, "load_history", None) or store.load
    return cached_read(store.path, loader)


def project_list(store):
    projects, warnings = [], []
    try:
        paths = sorted(store.directory.glob("*.json"))
        for path in paths:
            try:
                projects.append(cached_read(path, lambda path=path: store.load(path.stem)))
            except (ValueError, OSError, StorageError) as error:
                warnings.append(f"{path.name}: {error}")
    except OSError as error:
        raise StorageError("プロジェクト一覧を読み込めません。権限を確認してください。") from error
    projects.sort(key=lambda p: p.updated_at, reverse=True)
    return projects, warnings


def clear_cache():
    with _LOCK:
        _CACHE.clear()
        _STATS.update(hits=0, misses=0)


def cache_stats():
    with _LOCK:
        return dict(_STATS, entries=len(_CACHE), file_bytes=sum(item[0][2] if item[0] else 0 for item in _CACHE.values()))
