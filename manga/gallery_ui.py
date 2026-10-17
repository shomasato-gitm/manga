"""表示する画像だけを読み、元画像はダウンロード時に取り出す。"""

from io import BytesIO
from PIL import Image, ImageOps
from manga.ui_runtime import ui as st
from manga.ui_data import cached_read


def page_items(items, key, label, size=6):
    pages = max(1, (len(items)+size-1)//size)
    if pages == 1:return items
    page = st.selectbox(f"{label}の表示ページ", list(range(pages)), format_func=lambda n:f"{n+1}/{pages}（全{len(items)}件）", key=key)
    return items[page*size:(page+1)*size]


def thumbnail(store, image):
    def load():
        content = store.image_bytes(image)
        with Image.open(BytesIO(content)) as source:
            fitted = ImageOps.contain(source.convert("RGBA"), (360,360), Image.Resampling.LANCZOS)
            out=BytesIO();fitted.save(out,"PNG");return out.getvalue()
    return cached_read(store.image_path(image), load)
