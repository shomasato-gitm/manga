"""1プロジェクトを1つのJSONに保存する。外部サービスは利用しない。"""

import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Tuple

from manga.models import Project, now_iso, validate_id


class StorageError(Exception):
    """利用者に表示できる保存・読み込みエラー。"""


def write_json_atomic(path: Path, data: dict) -> None:
    """JSON全体を一時ファイルに書き、成功したときだけ置き換える。"""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise StorageError("保存できませんでした。保存先の権限と空き容量を確認してください。") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ProjectStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, project_id: str) -> Path:
        return self.directory / f"{validate_id(project_id)}.json"

    def save(self, project: Project) -> Project:
        """一時ファイルから置き換え、書き込み失敗時は元のJSONを守る。"""
        saved = replace(project, updated_at=now_iso())
        path = self._path(saved.id)
        write_json_atomic(path, saved.to_dict())
        return saved

    def load(self, project_id: str) -> Project:
        path = self._path(project_id)
        try:
            project = Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if project.id != project_id:
                raise ValueError("ファイル名とプロジェクトIDが一致しません。")
            return project
        except (OSError, ValueError) as error:
            raise StorageError(f"{path.name}を読み込めません。JSONの内容やファイルの権限を確認してください。") from error

    def list_projects(self) -> Tuple[List[Project], List[str]]:
        """壊れたファイルは警告にまとめ、正常なプロジェクトは表示する。"""
        projects, warnings = [], []
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError as error:
            raise StorageError("プロジェクト一覧を取得できません。保存先の権限を確認してください。") from error
        for path in paths:
            try:
                projects.append(self.load(path.stem))
            except (ValueError, StorageError) as error:
                warnings.append(f"{path.name}: {error}")
        projects.sort(key=lambda project: project.updated_at, reverse=True)
        return projects, warnings
