"""生成依頼→JSON取り込み→編集→ユーザーが選んだ項目だけを採用する。"""

import copy
from uuid import uuid4

from manga.assistant_changes import apply_items, context_unchanged
from manga.assistant_models import KINDS, normalize_item, normalize_payload, targets_for, validate_targets
from manga.assistant_prompts import production_prompt
from manga.assistant_repository import AssistantRepository
from manga.editorial_models import text
from manga.models import now_iso
from manga.storage import StorageError
from manga.story import load_json_payload


def record(**values):
    return dict(id=str(uuid4()), saved_at=now_iso(), **values)


class AssistantStore(AssistantRepository):
    def create_request(self, project, kind, *, expected_revision, targets=None, theme="", evaluation_id=None):
        if kind not in KINDS or project.id != self.project_id:
            raise ValueError("プロジェクトまたは依頼の種類が不正です。")
        with self.locked():
            data = self.checked(expected_revision)
            latest, artwork, editorial = self.latest(), self.artwork.load(), self.editorial.load()
            targets = validate_targets(targets if targets is not None else targets_for(latest["story"]), latest["story"]) if kind in ("directions", "evaluation", "improvement") else []
            if kind in ("evaluation", "improvement"):
                for target in targets:
                    direction = (artwork["panels"].get(f"{target['page']}:{target['panel']}") or {}).get("direction")
                    source = latest["story"]["pages"][target["page"] - 1]["panels"][target["panel"] - 1]
                    if not direction or direction["source_panel"] != source:
                        raise ValueError("対象コマに未設定または古い作画指示があります。先に設定を保存してください。")
            evaluation = None
            if kind == "improvement":
                evaluation = self.proposal(data, evaluation_id)
                base = self.request(data, evaluation["request_id"])
                if base["kind"] != "evaluation" or base["story_version_id"] != latest["id"]:
                    raise ValueError("現在のストーリーの作画評価を選んでください。")
                validate_targets(targets, latest["story"], base["targets"])
                context_unchanged(base, artwork, targets)
            request = record(project_id=self.project_id, kind=kind, story_version_id=latest["id"], targets=targets,
                             theme=text(theme, "テーマ補足", 2000), evaluation_id=evaluation_id,
                             context=copy.deepcopy(dict(story=latest["story"], artwork=artwork, references=editorial["references"])))
            request["prompt"] = production_prompt(project, request, evaluation)
            data["requests"].append(request)
            self.save(data)
            return request

    def import_proposal(self, request_id, raw, *, expected_revision):
        parsed = load_json_payload(raw)
        with self.locked():
            data = self.checked(expected_revision)
            request = self.request(data, request_id)
            self.latest(request["story_version_id"])
            payload = normalize_payload(parsed, request)
            if request["kind"] in ("directions", "evaluation", "improvement"):
                context_unchanged(request, self.artwork.load())
            if request["kind"] == "references" and any(item["confirmation_status"] != "要確認" for item in payload["items"]):
                raise ValueError("AIが返す参考作品は要確認で取り込んでください。確認済みへの変更は確認・編集画面で行えます。")
            proposal = record(request_id=request_id, payload=payload, original_payload=copy.deepcopy(payload), edits=[],
                              decisions={item["id"]: "確認待ち" for item in payload.get("items", [])})
            data["proposals"].append(proposal)
            self.save(data)
            return proposal

    def edit_item(self, proposal_id, item_id, values, *, expected_revision):
        with self.locked():
            data = self.checked(expected_revision)
            proposal = self.proposal(data, proposal_id)
            request = self.request(data, proposal["request_id"])
            if proposal["decisions"].get(item_id) != "確認待ち":
                raise ValueError("確認待ちの提案だけを編集できます。")
            item = next(item for item in proposal["payload"]["items"] if item["id"] == item_id)
            updated = normalize_item(request["kind"], values, request)
            if updated["id"] != item_id or request["kind"] in ("directions", "improvement") and (updated["page"], updated["panel"]) != (item["page"], item["panel"]):
                raise ValueError("提案IDや対象コマは変更できません。")
            proposal["edits"].append(dict(item_id=item_id, saved_at=now_iso(), before=copy.deepcopy(item), after=copy.deepcopy(updated)))
            item.clear()
            item.update(updated)
            self.save(data)

    def decide(self, proposal_id, selected_ids, status, *, expected_revision):
        if status not in ("見送り", "確認待ち") or not selected_ids or len(set(selected_ids)) != len(selected_ids):
            raise ValueError("見送る・確認待ちへ戻す提案を選択してください。")
        with self.locked():
            data = self.checked(expected_revision)
            proposal = self.proposal(data, proposal_id)
            for item_id in selected_ids:
                if proposal["decisions"].get(item_id) not in ("見送り", "確認待ち"):
                    raise ValueError("採用済みの提案は変更できません。")
                proposal["decisions"][item_id] = status
            self.save(data)

    def adopt(self, proposal_id, selected_ids, *, expected_revision, expected_artwork_revision, expected_editorial_revision, allow_duplicates=False):
        if not selected_ids or len(set(selected_ids)) != len(selected_ids):
            raise ValueError("採用する提案を重複なく選択してください。")
        with self.locked():
            before = self.checked(expected_revision)
            data = copy.deepcopy(before)
            proposal = self.proposal(data, proposal_id)
            request = self.request(data, proposal["request_id"])
            self.latest(request["story_version_id"])
            if request.get("character_ai"):
                raise ValueError("この人物提案は「AIキャラクター制作・一貫性管理」で人物の同定・固定ロック・部分採用を確認してください。")
            artwork, editorial = self.artwork.load(), self.editorial.load()
            if type(expected_artwork_revision) is not int or artwork["revision"] != expected_artwork_revision or type(expected_editorial_revision) is not int or editorial["revision"] != expected_editorial_revision:
                raise StorageError("既存設定が更新されました。画面を更新して差分を確認してください。")
            if any(proposal["decisions"].get(item_id) != "確認待ち" for item_id in selected_ids):
                raise ValueError("確認待ちの提案だけを採用できます。採用済みの提案は二重登録しません。")
            selected = [item for item in proposal["payload"]["items"] if item["id"] in selected_ids]
            target, changes = apply_items(request["kind"], selected, request, artwork, editorial, allow_duplicates=allow_duplicates)
            for item_id in selected_ids:
                proposal["decisions"][item_id] = "採用済み"
            target_name = "editorial" if request["kind"] == "references" else "artwork"
            data["adoptions"].append(record(proposal_id=proposal_id, target=target_name, changes=changes))
            self.commit_adoption(before, data, target_name, editorial if target_name == "editorial" else artwork, target)
            return changes
