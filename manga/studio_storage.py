"""UI設定と実際に生成した成果物の記録。第1〜5段階のJSONは変更しない。"""

import fcntl
import json
from pathlib import Path
from datetime import datetime

from manga.models import now_iso, validate_id
from manga.storage import StorageError, write_json_atomic


class StudioStore:
    def __init__(self, directory, project_id):
        self.project_id = validate_id(project_id)
        self.path = Path(directory) / project_id / "studio.json"

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["project_id"] != self.project_id or type(data["revision"]) is not int or data["revision"] < 0 or not isinstance(data["renders"], dict) or not isinstance(data["exports"], list) or type(data["skip_evaluation"]) is not bool:
                raise ValueError
            for key,event in data["renders"].items():
                if not key.isdecimal() or not 1 <= int(key) <= 100:raise ValueError
                self.validate_event(event)
            for event in data["exports"]:
                self.validate_event(event)
                if event["kind"] not in ("page","pdf","png_zip","sns") or not isinstance(event["pages"],list) or not event["pages"] or any(type(p) is not int or not 1 <= p <= 100 for p in event["pages"]):raise ValueError
            return data
        except FileNotFoundError:
            return dict(schema_version=1, project_id=self.project_id, revision=0, skip_evaluation=False, renders={}, exports=[])
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise StorageError("進行状況の設定を読み込めません。studio.jsonのバックアップを確認してください。") from error

    @staticmethod
    def validate_event(event):
        fp=event["fingerprint"]
        if not isinstance(fp,list) or len(fp)!=4:raise ValueError
        validate_id(fp[0])
        if any(type(v) is not int or v<0 for v in fp[1:3]) or not isinstance(fp[3],str):raise ValueError
        if datetime.fromisoformat(event["saved_at"]).tzinfo is None:raise ValueError

    def update(self, action):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with (self.path.parent / ".studio.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    data = self.load()
                    action(data)
                    data["revision"] += 1
                    write_json_atomic(self.path, data)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError as error:
            raise StorageError("進行状況を保存できません。保存先と空き容量を確認してください。") from error

    def record_render(self, page, fingerprint):
        if self.load()["renders"].get(str(page), {}).get("fingerprint") == fingerprint:
            return
        self.update(lambda data: data["renders"].update({str(page):dict(fingerprint=fingerprint, saved_at=now_iso())}))

    def record_export(self, event):
        def action(data):
            saved_at=now_iso()
            data["exports"].append(dict(event,saved_at=saved_at))
            for page in event["pages"]:
                data["renders"][str(page)]=dict(fingerprint=event["fingerprint"],saved_at=saved_at)
        self.update(action)
