"""ストーリー全体の履歴を、プロジェクト別のローカルJSONに保存する。"""

import copy
import fcntl
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.story import normalize_story


class StoryStore:
    def __init__(self, directory: Path, project_id: str):
        self.project_id = validate_id(project_id)
        self.directory = Path(directory) / self.project_id
        self.path = self.directory / "story_history.json"

    def load_history(self) -> list:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as error:
            raise StorageError("ストーリー履歴を読み込めません。保存先の権限を確認してください。") from error
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1 or data.get("project_id") != self.project_id:
                raise ValueError
            versions = data["versions"]
            if not isinstance(versions, list) or not versions:
                raise ValueError
            seen = set()
            for version in versions:
                validate_id(version["id"])
                if version["id"] in seen or version["action"] not in ("取り込み", "ページ編集", "復元"):
                    raise ValueError
                if datetime.fromisoformat(version["saved_at"]).tzinfo is None:
                    raise ValueError
                restored_from = version.get("restored_from")
                if version["action"] == "復元":
                    if restored_from not in seen:
                        raise ValueError
                elif restored_from is not None:
                    raise ValueError
                normalized = normalize_story(version["story"]).story
                if normalized != version["story"]:
                    raise ValueError
                seen.add(version["id"])
            return versions
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError("ストーリー履歴の形式が不正です。既存ファイルを上書きせず、バックアップから確認してください。") from error

    @contextmanager
    def _lock(self):
        """macOS/Linux共通のロックで同時保存による履歴の欠落を防ぐ。"""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / ".story.lock").open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise StorageError("ストーリーを保存できません。保存先の権限と空き容量を確認してください。") from error

    def _append(self, history, story, action, expected_version_id, restored_from=None):
        latest_id = history[-1]["id"] if history else None
        if latest_id != expected_version_id:
            raise StorageError("別の保存で履歴が更新されました。画面を更新し、最新の内容を確認してから保存し直してください。")
        version = {
            "id": str(uuid4()),
            "saved_at": now_iso(),
            "action": action,
            "restored_from": restored_from,
            "story": normalize_story(copy.deepcopy(story)).story,
        }
        write_json_atomic(self.path, {"schema_version": 1, "project_id": self.project_id, "versions": history + [version]})
        return version

    def save(self, story: dict, *, expected_version_id, action="取り込み") -> dict:
        if action not in ("取り込み", "ページ編集"):
            raise ValueError("保存の種類が不正です。")
        with self._lock():
            return self._append(self.load_history(), story, action, expected_version_id)

    def restore(self, version_id: str, *, expected_version_id) -> dict:
        validate_id(version_id)
        with self._lock():
            history = self.load_history()
            selected = next((version for version in history if version["id"] == version_id), None)
            if selected is None:
                raise StorageError("復元するバージョンが見つかりません。履歴を確認してください。")
            return self._append(history, selected["story"], "復元", expected_version_id, version_id)
