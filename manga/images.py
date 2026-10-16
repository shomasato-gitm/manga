"""アップロード画像を実際に検査し、メタデータを除いたPNGへ変換する。"""

import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000


def prepare_image(content: bytes):
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("画像は空でない20MB以下のファイルにしてください。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP"):
                    raise ValueError("PNG・JPEG・WebPの静止画を選択してください。")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("画像は2400万画素以下にしてください。")
                if getattr(image, "is_animated", False):
                    raise ValueError("アニメーションではなく静止画を選択してください。")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                oriented = ImageOps.exif_transpose(image)
                mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
                converted = oriented.convert(mode)
                clean = Image.new(mode, converted.size)
                clean.paste(converted)
                output = BytesIO()
                clean.save(output, format="PNG")
                if output.tell() > MAX_IMAGE_BYTES:
                    raise ValueError("PNG変換後に20MBを超えます。画像を小さくして再登録してください。")
                return output.getvalue(), clean.width, clean.height
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("画像を読み込めません。壊れていないPNG・JPEG・WebPの静止画を選択してください。") from None
