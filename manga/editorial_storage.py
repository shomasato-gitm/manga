"""プロジェクト別の参考作品・分析・評価・脚本確定履歴。既存の脚本や画像は別保存。"""

import copy
import fcntl
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from manga.editorial_models import (
    CRITERIA, MODES, normalize_analysis, normalize_evaluation, normalize_targets,
    parse_analysis, parse_evaluation, reference_fields, text,
)
from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.story import normalize_story, parse_story
from manga.story_comparison import check_improvement_scope
from manga.story_storage import StoryStore


def _stamp(value):
    if datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("日時にはタイムゾーンが必要です。")


def _record(**values):
    return dict(id=str(uuid4()), saved_at=now_iso(), **values)


class EditorialStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.root = Path(directory) / project_id
        self.path = self.root / "editorial.json"
        self.stories = StoryStore(directory, project_id)

    def load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(schema_version=1, project_id=self.project_id, revision=0,
                        references=[], analyses=[], evaluations=[], finalizations=[])
        except OSError as error:
            raise StorageError("参考作品・評価履歴を読み込めません。保存先の権限を確認してください。") from error
        try:
            return self.validate(json.loads(raw))
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError("参考作品・評価履歴の形式が不正です。上書きせず、バックアップから確認してください。") from error

    def validate(self, data):
        """採用の再開記録も、通常の読み込みと同じ規則で検証する。"""
        if not isinstance(data, dict) or type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["project_id"] != self.project_id:
            raise ValueError
        if type(data["revision"]) is not int or data["revision"] < 0:
            raise ValueError
        for key in ("references", "analyses", "evaluations", "finalizations"):
            if not isinstance(data[key], list):
                raise ValueError
        seen, refs = set(), {}
        for reference in data["references"]:
            validate_id(reference["id"])
            if reference["id"] in seen or type(reference["version"]) is not int or reference["version"] < 1:
                raise ValueError
            seen.add(reference["id"])
            if reference_fields(reference) != {key: reference[key] for key in reference_fields(reference)}:
                raise ValueError
            _stamp(reference["created_at"])
            _stamp(reference["updated_at"])
            if reference["deleted_at"] is not None:
                _stamp(reference["deleted_at"])
            refs[reference["id"]] = reference
        for analysis in data["analyses"]:
            self._validate_record(analysis, seen)
            payload = analysis["payload"]
            reference = refs[payload["reference_id"]]
            snapshot = analysis["reference_snapshot"]
            if snapshot["id"] != reference["id"] or type(snapshot["version"]) is not int or not 1 <= snapshot["version"] <= reference["version"]:
                raise ValueError
            reference_fields(snapshot)
            if payload["reference_version"] != snapshot["version"]:
                raise ValueError
            if normalize_analysis(payload, reference["id"]) != payload:
                raise ValueError
        for evaluation in data["evaluations"]:
            self._validate_record(evaluation, seen)
            payload = evaluation["payload"]
            validate_id(payload["story_version_id"])
            source = normalize_story(evaluation["source_story"]).story
            if source != evaluation["source_story"] or normalize_evaluation(payload, payload["story_version_id"], source) != payload:
                raise ValueError
        for final in data["finalizations"]:
            self._validate_record(final, seen)
            validate_id(final["story_version_id"])
            if normalize_story(final["source_story"]).story != final["source_story"]:
                raise ValueError
        return data

    @staticmethod
    def _validate_record(record, seen):
        validate_id(record["id"])
        if record["id"] in seen:
            raise ValueError
        seen.add(record["id"])
        _stamp(record["saved_at"])

    def _update(self, operation, expected_revision):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".editorial.lock").open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    data = self.load()
                    if type(expected_revision) is not int or data["revision"] != expected_revision:
                        raise StorageError("参考作品・評価履歴が更新されました。画面を更新してから保存し直してください。")
                    result = operation(data)
                    data["revision"] += 1
                    write_json_atomic(self.path, data)
                    return copy.deepcopy(result)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise StorageError("参考作品・評価履歴を保存できません。権限と空き容量を確認してください。") from error

    @staticmethod
    def _reference(data, reference_id):
        reference = next((item for item in data["references"] if item["id"] == reference_id and not item["deleted_at"]), None)
        if reference is None:
            raise ValueError("参考作品が見つからないか、削除済みです。")
        return reference

    def save_reference(self, fields, *, reference_id=None, expected_revision):
        fields = reference_fields(fields)

        def operation(data):
            if reference_id:
                reference = self._reference(data, reference_id)
                reference.update(fields, version=reference["version"] + 1, updated_at=now_iso())
            else:
                reference = dict(fields, id=str(uuid4()), version=1, created_at=now_iso(), updated_at=now_iso(), deleted_at=None)
                data["references"].append(reference)
            return reference

        return self._update(operation, expected_revision)

    def delete_reference(self, reference_id, *, expected_revision):
        def operation(data):
            reference = self._reference(data, reference_id)
            reference["deleted_at"] = now_iso()
            return reference

        return self._update(operation, expected_revision)

    def save_analysis(self, reference_id, raw, *, expected_revision):
        payload = parse_analysis(raw, reference_id)

        def operation(data):
            reference = self._reference(data, reference_id)
            if payload["reference_version"] != reference["version"]:
                raise ValueError("参考作品の登録内容が更新されています。現在の版の分析プロンプトから分析し直してください。")
            record = _record(payload=payload, reference_snapshot=copy.deepcopy(reference))
            data["analyses"].append(record)
            return record

        return self._update(operation, expected_revision)

    def version(self, version_id):
        version = next((item for item in self.stories.load_history() if item["id"] == version_id), None)
        if version is None:
            raise ValueError("対象のストーリーバージョンが見つかりません。")
        return version

    def save_evaluation(self, version_id, raw, *, expected_revision):
        version = self.version(version_id)
        payload = parse_evaluation(raw, version_id, version["story"])

        def operation(data):
            record = _record(payload=payload, source_story=copy.deepcopy(version["story"]))
            data["evaluations"].append(record)
            return record

        return self._update(operation, expected_revision)

    def save_improvement(self, raw, base_id, evaluation_id, mode, criteria, targets, reason, *, expected_version_id):
        base = self.version(base_id)
        evaluation = next((item for item in self.load()["evaluations"] if item["id"] == evaluation_id), None)
        if evaluation is None or evaluation["payload"]["story_version_id"] != base_id or evaluation["source_story"] != base["story"]:
            raise ValueError("評価と改善元のストーリー版が一致しません。対象の版を評価してください。")
        if mode not in MODES or not isinstance(criteria, list) or not criteria or any(key not in CRITERIA for key in criteria):
            raise ValueError("改善方式と改善する評価項目を選択してください。")
        targets = normalize_targets(targets, base["story"], required=mode == MODES[1])
        improved = parse_story(raw, len(base["story"]["pages"]), base["story"]["title"])
        check_improvement_scope(base["story"], improved.story, mode, targets)
        reason = text(reason, "改善理由", required=True)
        context = dict(base_version_id=base_id, evaluation_id=evaluation_id, mode=mode,
                       criteria=criteria, targets=targets, reason=reason)
        version = self.stories.save(improved.story, expected_version_id=expected_version_id, revision_context=context)
        return version, improved.warnings

    def finalize(self, *, expected_version_id, expected_revision):
        # 脚本の保存と確定が同時に走っても、確認した版だけを確定する。
        with self.stories._lock():
            history = self.stories.load_history()
            if not history or history[-1]["id"] != expected_version_id:
                raise StorageError("ストーリーが更新されました。現在の版を確認してから確定してください。")

            def operation(data):
                record = _record(story_version_id=expected_version_id, source_story=copy.deepcopy(history[-1]["story"]))
                data["finalizations"].append(record)
                return record

            return self._update(operation, expected_revision)


def current_analysis(data, reference):
    return next((item for item in reversed(data["analyses"]) if item["payload"]["reference_id"] == reference["id"]
                 and item["reference_snapshot"]["version"] == reference["version"]), None)


def generation_references(data, selected_ids):
    if len(selected_ids) > 3 or len(set(selected_ids)) != len(selected_ids):
        raise ValueError("参考作品は重複なく3作品まで選択してください。")
    result = []
    for reference_id in selected_ids:
        reference = EditorialStore._reference(data, reference_id)
        analysis = current_analysis(data, reference)
        techniques = [reference["techniques"]] if reference["techniques"] else []
        if analysis:
            techniques += [item["abstract_technique"] for item in analysis["payload"]["aspects"].values()
                           if item["status"] == "ユーザー要約に基づく" and item["abstract_technique"]]
        result.append({"title": reference["title"], "techniques": techniques, "avoid": reference["avoid"]})
    return result
