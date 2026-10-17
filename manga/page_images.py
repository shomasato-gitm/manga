"""コマ内の画像配置。旧ページはcontain、新規ページは明示したcoverを使う。"""
from PIL import Image, ImageOps

LEGACY_PLACEMENT = dict(mode="contain", x=0.5, y=0.5, zoom=1.0)
MODES = {"cover": "コマを埋める（トリミングあり）", "contain": "画像全体を表示（余白あり）"}


def placement_for(config, panel):
    return dict(LEGACY_PLACEMENT, **config.get("image_placements", {}).get(str(panel), {}))


def place_image(source, size, placement):
    """coverは可視領域だけをリサンプルする。元画像の画素・ファイルは変更しない。"""
    width, height = size
    if placement["mode"] == "contain":
        fitted = ImageOps.contain(source, size, Image.Resampling.LANCZOS)
        position = (int((width-fitted.width)*placement["x"]), int((height-fitted.height)*placement["y"]))
        return fitted, position
    scale = max(width/source.width, height/source.height)*placement["zoom"]
    visible_w, visible_h = width/scale, height/scale
    left = (source.width-visible_w)*placement["x"]
    top = (source.height-visible_h)*placement["y"]
    box = (max(0,left), max(0,top), min(source.width,left+visible_w), min(source.height,top+visible_h))
    return source.resize(size, Image.Resampling.LANCZOS, box=box), (0,0)
