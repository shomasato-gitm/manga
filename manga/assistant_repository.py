"""制作提案の保存基盤。採用時は書き込み前の記録を残し、途中失敗を再開する。"""

import fcntl
import json
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path

from manga.artwork_models import validate_artwork
from manga.artwork_storage import ArtworkStore
from manga.assistant_models import KINDS, normalize_payload, validate_targets
from manga.editorial_storage import EditorialStore
from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.story import normalize_story
from manga.story_storage import StoryStore


def empty_assistant(project_id):
    return dict(schema_version=1, project_id=project_id, revision=0, requests=[], proposals=[], adoptions=[])


def validate_assistant(data, project_id):
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["project_id"] != project_id or type(data["revision"]) is not int or data["revision"] < 0:
        raise ValueError("制作提案の形式が不正です。")
    requests, proposals, seen = {}, {}, set()
    for collection in ("requests", "proposals", "adoptions"):
        if not isinstance(data[collection], list):
            raise ValueError
        for record in data[collection]:
            validate_id(record["id"])
            if record["id"] in seen or datetime.fromisoformat(record["saved_at"]).tzinfo is None:
                raise ValueError
            seen.add(record["id"])
    for request in data["requests"]:
        if request["project_id"] != project_id or request["kind"] not in KINDS:
            raise ValueError
        validate_id(request["story_version_id"])
        context = request["context"]
        if normalize_story(context["story"]).story != context["story"]:
            raise ValueError
        validate_artwork(context["artwork"], project_id)
        if request["kind"] in ("directions", "evaluation", "improvement"):
            validate_targets(request["targets"], context["story"])
        elif request["targets"] != []:
            raise ValueError
        if not isinstance(request["theme"], str) or not isinstance(request["prompt"], str):
            raise ValueError
        if request.get('character_ai',{}).get('provider')=='gemini-text':
            from manga.character_ai_gemini import validate_record
            validate_record(request)
        requests[request["id"]] = request
    for proposal in data["proposals"]:
        request = requests[proposal["request_id"]]
        if normalize_payload(proposal["payload"], request) != proposal["payload"] or normalize_payload(proposal["original_payload"], request) != proposal["original_payload"]:
            raise ValueError
        expected = {item["id"] for item in proposal["payload"].get("items", [])}
        if set(proposal["decisions"]) != expected or not isinstance(proposal["edits"], list):
            raise ValueError
        if any(status not in ("確認待ち", "採用済み", "見送り") for status in proposal["decisions"].values()):
            raise ValueError
        proposals[proposal["id"]] = proposal
    for request in requests.values():
        if request["kind"] == "improvement":
            evaluation = proposals[request["evaluation_id"]]
            if requests[evaluation["request_id"]]["kind"] != "evaluation":
                raise ValueError
    for adoption in data["adoptions"]:
        proposal = proposals[adoption["proposal_id"]]
        if adoption["target"] not in ("artwork", "editorial") or not isinstance(adoption["changes"], list):
            raise ValueError
        for change in adoption["changes"]:
            if proposal["decisions"][change["item_id"]] != "採用済み":
                raise ValueError
    return data


class AssistantRepository:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.root = Path(directory) / project_id
        self.path = self.root / "assistant.json"
        self.journal_path = self.root / "assistant_pending.json"
        self.artwork = ArtworkStore(directory, project_id)
        self.editorial = EditorialStore(directory, project_id)
        self.stories = StoryStore(directory, project_id)

    def load(self):
        try:
            return validate_assistant(json.loads(self.path.read_text(encoding="utf-8")), self.project_id)
        except FileNotFoundError:
            return empty_assistant(self.project_id)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError("制作アシスタントJSONを読み込めません。既存ファイルを上書きせずバックアップを確認してください。") from error

    @contextmanager
    def locked(self):
        # 全操作で同じ順序を使う。ストーリー保存・既存画面の保存とも競合を防ぐ。
        with self.stories._lock():
            try:
                with ExitStack() as stack:
                    for name in ("artwork", "editorial", "assistant"):
                        file = stack.enter_context((self.root / f".{name}.lock").open("a"))
                        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
                        stack.callback(fcntl.flock, file.fileno(), fcntl.LOCK_UN)
                    yield
            except OSError as error:
                raise StorageError("制作提案を保存できません。権限・空き容量を確認してください。") from error

    def pending(self):
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            if journal["project_id"] != self.project_id or journal["status"] not in ("pending", "done") or journal["target"] not in ("artwork", "editorial"):
                raise ValueError
            for key in ("before_assistant", "after_assistant"):
                validate_assistant(journal[key], self.project_id)
            for key in ("before_target", "after_target"):
                if journal["target"] == "artwork":
                    validate_artwork(journal[key], self.project_id)
                else:
                    self.editorial.validate(journal[key])
            return journal if journal["status"] == "pending" else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError("採用保存の再開記録が不正です。上書きせずバックアップを確認してください。") from error

    def checked(self, expected_revision):
        if self.pending():
            raise StorageError("採用保存が途中です。「採用保存を再開」で完了してから操作してください。")
        data = self.load()
        if type(expected_revision) is not int or data["revision"] != expected_revision:
            raise StorageError("制作提案が更新されました。画面を更新して保存し直してください。")
        return data

    def latest(self, version_id=None):
        history = self.stories.load_history()
        if not history:
            raise ValueError("先にストーリーJSONを登録してください。")
        if version_id is not None and history[-1]["id"] != version_id:
            raise StorageError("ストーリーが変更されています。古い制作提案は採用できません。最新の版で依頼を作り直してください。")
        return history[-1]

    def save(self, data):
        data["revision"] += 1
        validate_assistant(data, self.project_id)
        write_json_atomic(self.path, data)

    def commit_adoption(self, before, after, target, before_target, after_target):
        after["revision"] += 1
        validate_assistant(after, self.project_id)
        if target == "artwork":
            validate_artwork(after_target, self.project_id)
        else:
            self.editorial.validate(after_target)
        journal = dict(project_id=self.project_id, status="pending", target=target, saved_at=now_iso(),
                       before_assistant=before, after_assistant=after, before_target=before_target, after_target=after_target)
        # 意図を先に永続化する。以降のどの書き込みで停止しても明示操作で再開できる。
        write_json_atomic(self.journal_path, journal)
        self._finish(journal)

    def _finish(self, journal):
        if journal["after_assistant"]["adoptions"] and journal["after_assistant"]["adoptions"][-1].get("character_ai"):
            from manga.character_ai_recovery import validate_recovery
            validate_recovery(journal)
        target_store = self.artwork if journal["target"] == "artwork" else self.editorial
        current_target, current_assistant = target_store.load(), self.load()
        if current_target not in (journal["before_target"], journal["after_target"]) or current_assistant not in (journal["before_assistant"], journal["after_assistant"]):
            raise StorageError("保存途中に別の編集が行われました。自動上書きは停止します。assistant_pending.jsonと各JSONをバックアップして差分を確認してください。")
        if current_target != journal["after_target"]:
            write_json_atomic(target_store.path, journal["after_target"])
        if current_assistant != journal["after_assistant"]:
            write_json_atomic(self.path, journal["after_assistant"])
        write_json_atomic(self.journal_path, dict(journal, status="done"))

    def resume(self):
        with self.locked():
            pending = self.pending()
            if pending:
                self._finish(pending)
        return True

    @staticmethod
    def request(data, request_id):
        record = next((item for item in data["requests"] if item["id"] == request_id), None)
        if record is None:
            raise ValueError("生成依頼が見つかりません。")
        return record

    @staticmethod
    def proposal(data, proposal_id):
        record = next((item for item in data["proposals"] if item["id"] == proposal_id), None)
        if record is None:
            raise ValueError("提案履歴が見つかりません。")
        return record
