from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image, PngImagePlugin

from manga.images import MAX_IMAGE_BYTES, prepare_image


def image_bytes(format="PNG", color="red"):
    output = BytesIO()
    Image.new("RGB", (24, 32), color).save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_supported_images_become_png(format):
    png, width, height = prepare_image(image_bytes(format))
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (24, 32) == (width, height)


def test_orientation_applied_and_metadata_removed():
    source = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private author"
    Image.new("RGB", (24, 32), "blue").save(source, format="JPEG", exif=exif)
    png, width, height = prepare_image(source.getvalue())
    assert (width, height) == (32, 24)
    with Image.open(BytesIO(png)) as image:
        assert not image.getexif()


def test_png_comments_removed_and_alpha_preserved():
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "not retained")
    Image.new("RGBA", (12, 12), (255, 0, 0, 70)).save(output, format="PNG", pnginfo=metadata)
    png, _, _ = prepare_image(output.getvalue())
    with Image.open(BytesIO(png)) as image:
        assert "private" not in image.info
        assert image.getpixel((0, 0))[3] == 70


@pytest.mark.parametrize("content", [b"", b"not an image", b"<svg></svg>", image_bytes()[:40], b"x" * (MAX_IMAGE_BYTES + 1)])
def test_invalid_upload_rejected(content):
    with pytest.raises(ValueError):
        prepare_image(content)


def test_unsupported_format_rejected():
    with pytest.raises(ValueError, match="PNG"):
        prepare_image(image_bytes("BMP"))


def test_pixel_limit_checked_before_decode():
    with patch("manga.images.MAX_IMAGE_PIXELS", 10):
        with pytest.raises(ValueError, match="画素"):
            prepare_image(image_bytes())


def test_animation_rejected():
    output = BytesIO()
    Image.new("RGB", (16, 16), "red").save(output, format="PNG", save_all=True,
                                         append_images=[Image.new("RGB", (16, 16), "blue")], duration=100)
    with pytest.raises(ValueError, match="静止画"):
        prepare_image(output.getvalue())
