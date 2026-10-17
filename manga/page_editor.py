"""ビジュアル編集の下書きと、ブラウザから受け取る編集操作の検証。保存処理とは分離する。"""
import copy
import hashlib
import json
from uuid import uuid4
from manga.page_layout import validate_page, validate_rect


def config_hash(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def new_draft(config, revision, version_id):
    return dict(config=copy.deepcopy(config),base=copy.deepcopy(config),revision=revision,
                version_id=version_id,epoch=str(uuid4()),selected=None,proposal=None)


def event_token(draft):
    return f"{draft['epoch']}:{config_hash(draft['config'])}"


def canvas_event(config,event,token):
    if not isinstance(event,dict) or event.get("token") != token:
        raise ValueError("以前のプレビューからの操作です。最新の表示でもう一度操作してください。")
    if event.get("type") not in ("select","move") or not isinstance(event.get("event_id"),str) or not 1 <= len(event["event_id"]) <= 100:
        raise ValueError("ページ上の編集操作の形式が不正です。")
    selected=event.get("id")
    if selected not in {b["id"] for b in config["bubbles"]}:
        raise ValueError("操作対象の吹き出しが現在のページにありません。")
    changed=copy.deepcopy(config)
    if event["type"] == "move":
        if not isinstance(event.get("box"),dict):raise ValueError("吹き出しの座標が不正です。")
        try:validate_rect(event["box"],"吹き出し",.10)
        except (KeyError,TypeError) as error:raise ValueError("吹き出しの位置・サイズが欠けています。もう一度操作してください。") from error
        for bubble in changed["bubbles"]:
            if bubble["id"] == selected:bubble["box"]={k:event["box"][k] for k in ("x","y","width","height")}
        validate_page(changed)
    return changed,selected


def replace_bubbles(config,bubbles):
    changed=copy.deepcopy(config)
    changed["bubbles"]=copy.deepcopy(bubbles)
    changed["editor_version"]=2
    return validate_page(changed)
