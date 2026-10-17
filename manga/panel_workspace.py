"""コマの移動・下書きの識別。画像合成とは独立した、表示用の小さなモデル。"""
import hashlib
import json
from manga.artwork_models import panel_key


def panel_targets(story):
    return [(p["page"], c["panel"]) for p in story["pages"] for c in p["panels"]]


def neighbor(story, page, panel, offset):
    targets = panel_targets(story)
    index = targets.index((page, panel)) + offset
    return targets[index] if 0 <= index < len(targets) else None


def next_unfinished(rows, current):
    """現在位置の後から探し、末尾なら先頭へ戻る。古い採用画像も未完了扱い。"""
    targets = [r["key"] for r in rows]
    index = targets.index(current)
    ordered = rows[index+1:] + rows[:index+1]
    row = next((r for r in ordered if not r["selected"] or not r["direction"] or r["stale"]), None)
    return (row["page"], row["panel"]) if row else None


def draft_token(version_id, page, panel, direction):
    # 別コマの保存・画像追加・画像採用で下書きを失わない。自身の確定指示の更新時だけ分ける。
    digest = hashlib.sha256(json.dumps(direction, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return f"{version_id}_{panel_key(page,panel)}_{digest}"


def overview_rows(rows):
    return [{"ページ": r["page"], "コマ": r["panel"],
             "作画指示": "作画指示がある" if r["direction"] else "作画指示がない",
             "画像": "画像を採用済み" if r["selected"] else "画像候補がある" if r.get("candidates", 0) > 0 else "画像が未登録",
             "確認": "要確認" if r["stale"] else ""} for r in rows]
