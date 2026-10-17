"""元画像を読まずに成果物の更新を検知する。"""
import hashlib
import json
import os
from pathlib import Path
from manga.ui_data import signature


def asset_token(root, artwork, settings):
    root=Path(root)
    files=[]
    for entry in artwork["panels"].values():
        for image in entry["candidates"]:
            if image["id"]==entry["selected_image_id"]:
                path=root/image["path"]
                files.append((image["id"],signature(path) if path.resolve().is_relative_to(root.resolve()) else "unsafe"))
    configured=settings["font_path"] or os.environ.get("MANGA_FONT_PATH","")
    fonts=[Path(configured).expanduser()] if configured else [Path(p) for p in ("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc","/System/Library/Fonts/ヒラギノ明朝 ProN.ttc","/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc","/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc")]
    files.extend((str(font),signature(font)) for font in fonts)
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()


def production_fingerprint(latest,artwork,data,root):
    return [latest["id"],artwork["revision"],data["revision"],asset_token(root,artwork,data["settings"])]
