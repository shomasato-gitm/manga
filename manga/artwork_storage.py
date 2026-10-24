"""プロジェクト別のキャラクター・作画指示・候補画像をローカル保存する。"""

import copy
import fcntl
import json
import os
from pathlib import Path
from uuid import uuid4

from manga.artwork_models import character_fields, direction_fields, empty_artwork, panel_key, text, validate_artwork
from manga.images import prepare_image
from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic
from manga.story_storage import StoryStore


class ArtworkStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.root = Path(directory) / self.project_id
        self.path = self.root / "artwork.json"
        self.story_store = StoryStore(directory, project_id)

    def load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty_artwork(self.project_id)
        except OSError as error:
            raise StorageError("作画設定を読み込めません。保存先の権限を確認してください。") from error
        try:
            return validate_artwork(json.loads(raw), self.project_id)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise StorageError("作画設定の形式が不正です。既存のJSONや画像を上書きせず、バックアップから確認してください。 " + str(error)) from error

    def _update(self, operation, expected_revision):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / ".artwork.lock").open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    data = self.load()
                    if data["revision"] != expected_revision:
                        raise StorageError("別の保存で作画設定が更新されました。画面を更新してから保存し直してください。")
                    result = operation(data)
                    data["revision"] += 1
                    validate_artwork(data, self.project_id)
                    write_json_atomic(self.path, data)
                    return copy.deepcopy(result)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError as error:
            raise StorageError("作画設定を保存できません。保存先の権限と空き容量を確認してください。") from error

    @staticmethod
    def _character(data, character_id, include_deleted=False):
        validate_id(character_id)
        character = next((item for item in data["characters"] if item["id"] == character_id), None)
        if character is None or (character["deleted_at"] and not include_deleted):
            raise ValueError("キャラクターが見つからないか、削除済みです。作画指示を更新してください。")
        return character

    def save_style(self, style, *, expected_revision):
        style = text(style, "作風", required=True)

        def operation(data):
            data["style"] = style
            return style

        return self._update(operation, expected_revision)

    def save_character(self, fields, *, expected_revision, character_id=None):
        fields = character_fields(fields)

        def operation(data):
            if character_id:
                character = self._character(data, character_id)
                character.update(fields, updated_at=now_iso())
            else:
                character = dict(fields, id=str(uuid4()), created_at=now_iso(), updated_at=now_iso(),
                                 deleted_at=None, references=[], primary_reference_id=None)
                data["characters"].append(character)
            return character

        return self._update(operation, expected_revision)

    def delete_character(self, character_id, *, expected_revision):
        def operation(data):
            character = self._character(data, character_id)
            character["deleted_at"] = now_iso()
            return character

        return self._update(operation, expected_revision)

    def _current_panel(self, page, panel, story_version_id):
        panel_key(page, panel)
        history = self.story_store.load_history()
        if not history or history[-1]["id"] != story_version_id:
            raise StorageError("ストーリーが更新されました。最新のページ・コマを確認してから保存してください。")
        try:
            return copy.deepcopy(history[-1]["story"]["pages"][page - 1]["panels"][panel - 1])
        except IndexError:
            raise ValueError("指定したページ・コマがストーリーにありません。") from None

    @staticmethod
    def _entry(data, page, panel):
        return data["panels"].setdefault(panel_key(page, panel), {
            "page": page, "panel": panel, "direction": None, "candidates": [],
            "selected_image_id": None, "selection_history": [],
        })

    def save_direction(self, page, panel, fields, *, story_version_id, expected_revision):
        fields = direction_fields(fields)

        def operation(data):
            for character_id in fields["character_ids"]:
                self._character(data, character_id)
            source = self._current_panel(page, panel, story_version_id)
            direction = dict(fields, story_version_id=story_version_id, source_panel=source, updated_at=now_iso())
            self._entry(data, page, panel)["direction"] = direction
            return direction

        return self._update(operation, expected_revision)

    def _add_image(self, content, filename, category, operation, expected_revision):
        png, width, height = prepare_image(content)
        image_id = str(uuid4())
        relative = f"images/{category}/{image_id}.png"
        path = self.root / relative
        original_name = Path(filename.replace("\\", "/")).name[:200] or "image"
        image = dict(id=image_id, path=relative, original_name=original_name, width=width, height=height, saved_at=now_iso())
        created = False

        def save(data):
            nonlocal created
            result = operation(data, image)
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise StorageError("画像の保存先がプロジェクトの外側です。")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as file:
                created = True
                file.write(png)
                file.flush()
                os.fsync(file.fileno())
            return result

        try:
            return self._update(save, expected_revision)
        except Exception:
            # この操作で新規作成した未登録ファイルだけを片付ける。過去の画像は削除しない。
            if created:
                path.unlink(missing_ok=True)
            raise

    def add_reference(self, character_id, content, filename, view, note="", *, expected_revision):
        view = text(view, "参照画像の種類", 100, required=True)
        note = text(note, "参照画像のメモ", 500)

        def operation(data, image):
            character = self._character(data, character_id)
            image.update(view=view, note=note)
            character["references"].append(image)
            if character["primary_reference_id"] is None:
                character["primary_reference_id"] = image["id"]
            return image

        return self._add_image(content, filename, "references", operation, expected_revision)

    def select_reference(self, character_id, image_id, *, expected_revision):
        def operation(data):
            character = self._character(data, character_id)
            if image_id not in {image["id"] for image in character["references"]}:
                raise ValueError("選択した参照画像が見つかりません。")
            character["primary_reference_id"] = image_id
            return image_id

        return self._update(operation, expected_revision)

    def add_panel_image(self, page, panel, content, filename, *, story_version_id, expected_revision):
        def operation(data, image):
            source = self._current_panel(page, panel, story_version_id)
            entry = self._entry(data, page, panel)
            direction = entry["direction"]
            characters = [copy.deepcopy(self._character(data, character_id, include_deleted=True))
                          for character_id in (direction["character_ids"] if direction else [])]
            image.update(story_version_id=story_version_id, source_panel=source,
                         context=copy.deepcopy({"style": data["style"], "direction": direction, "characters": characters}))
            entry["candidates"].append(image)
            return image

        return self._add_image(content, filename, "panels", operation, expected_revision)

    def select_panel_image(self, page, panel, image_id, *, expected_revision, story_version_id=None):
        def operation(data):
            if story_version_id is not None:
                self._current_panel(page, panel, story_version_id)
            entry = data["panels"].get(panel_key(page, panel))
            if entry is None or image_id not in {image["id"] for image in entry["candidates"]}:
                raise ValueError("選択した候補画像がこのコマにありません。")
            entry["selected_image_id"] = image_id
            entry["selection_history"].append({"image_id": image_id, "selected_at": now_iso()})
            return image_id

        return self._update(operation, expected_revision)

    def image_path(self, image):
        # キャッシュ利用前にも保存領域の内側であることを確認する。
        relative = image["path"]
        expected = {f"images/{category}/{validate_id(image['id'])}.png" for category in ("references", "panels")}
        if relative not in expected:
            raise StorageError("画像の保存先が不正です。")
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise StorageError("画像の保存先がプロジェクトの外側です。")
        return path

    def image_bytes(self, image):
        path = self.image_path(image)
        try:
            return path.read_bytes()
        except OSError as error:
            raise StorageError("画像ファイルを読み込めません。保存フォルダとバックアップを確認してください。") from error

    def restore_registered_image(self, image, content, expected_sha256, *, expected_revision):
        """保存中断で欠損した登録済みPNGだけを、同じIDで復元する。既存ファイルは変更しない。"""
        import hashlib
        png, width, height = prepare_image(content)
        if hashlib.sha256(png).hexdigest() != expected_sha256 or (width, height) != (image['width'], image['height']):
            raise StorageError('復元する画像のSHA・寸法が保存記録と一致しません。')

        def operation(data):
            registered = [i for c in data['characters'] for i in c['references']]
            registered += [i for e in data['panels'].values() for i in e['candidates']]
            if not any(i == image for i in registered):
                raise StorageError('登録済みの画像ID・メタデータが一致しません。新しいIDを発行せず停止しました。')
            path = self.image_path(image)
            for item in (self.root, path, *path.parents):
                if item == self.root.parent: break
                if item.is_symlink(): raise StorageError('登録画像の復元先のリンクを拒否しました。')
            if path.exists():
                if not path.is_file() or path.read_bytes() != png: raise StorageError('既存の異なる画像を上書きしません。')
                return image
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(png); stream.flush(); os.fsync(stream.fileno())
            return image

        return self._update(operation, expected_revision)
