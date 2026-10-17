"""明示的に要求された原寸だけを、ファイル変更を検知してキャッシュする。"""
from manga.ui_data import cached_read


def original_bytes(store, image):
    return cached_read(store.image_path(image), lambda: store.image_bytes(image), variant="original")
