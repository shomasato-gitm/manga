"""既存JSON・元画像を変更せず、ページ編集だけを別JSONへ保存する。"""

import copy
import fcntl
import json
from pathlib import Path

from manga.models import now_iso, validate_id
from manga.page_layout import DEFAULT_SETTINGS, validate_page, validate_settings
from manga.storage import StorageError, write_json_atomic
from manga.story_storage import StoryStore


class PageStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.root = Path(directory) / project_id
        self.path = self.root / "composition.json"
        self.stories = StoryStore(directory, project_id)

    def load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(schema_version=1, project_id=self.project_id, revision=0,
                        settings=copy.deepcopy(DEFAULT_SETTINGS), pages={}, history=[])
        except OSError as error:
            raise StorageError("ページ編集を読み込めません。保存先の権限を確認してください。") from error
        try:
            data = json.loads(raw)
            if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["project_id"] != self.project_id or type(data["revision"]) is not int or data["revision"] < 0:
                raise ValueError
            validate_settings(data["settings"])
            if not isinstance(data["pages"], dict) or not isinstance(data["history"], list):
                raise ValueError
            for key, page in data["pages"].items():
                validate_page(page)
                if key != str(page["source_page"]["page"]):
                    raise ValueError
            for event in data["history"]:
                if event["kind"] == "settings":
                    validate_settings(event["before"])
                elif event["kind"] == "page":
                    if event["before"] is not None:
                        validate_page(event["before"])
                else:
                    raise ValueError
            return data
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError("ページ編集JSONの形式が不正です。上書きせず、バックアップを確認してください。") from error

    def _update(self, operation, expected_revision):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".composition.lock").open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    data = self.load()
                    if type(expected_revision) is not int or data["revision"] != expected_revision:
                        raise StorageError("ページ編集が更新されました。画面を更新して保存し直してください。")
                    operation(data)
                    data["revision"] += 1
                    write_json_atomic(self.path, data)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise StorageError("ページ編集を保存できません。権限と空き容量を確認してください。") from error

    def save_settings(self, settings, *, expected_revision):
        validate_settings(settings)

        def operation(data):
            data["history"].append(dict(kind="settings", saved_at=now_iso(), before=copy.deepcopy(data["settings"])))
            data["settings"] = copy.deepcopy(settings)

        self._update(operation, expected_revision)

    def save_page(self, page_number, config, *, story_version_id, expected_revision):
        validate_page(config)

        def operation(data):
            history = self.stories.load_history()
            if not history or history[-1]["id"] != story_version_id:
                raise StorageError("ストーリーが更新されました。最新のページを確認してから保存してください。")
            if type(page_number) is not int or not 1 <= page_number <= len(history[-1]["story"]["pages"]):
                raise ValueError("対象ページがありません。")
            source = history[-1]["story"]["pages"][page_number - 1]
            if config["source_page"] != source or config["source_version_id"] != story_version_id:
                raise ValueError("ページ編集と元のストーリーが一致しません。")
            key = str(page_number)
            data["history"].append(dict(kind="page", page=page_number, saved_at=now_iso(), before=copy.deepcopy(data["pages"].get(key))))
            data["pages"][key] = copy.deepcopy(config)

        self._update(operation, expected_revision)
