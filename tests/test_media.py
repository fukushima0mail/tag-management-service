import io
import os

import pytest
from PIL import Image

from core.media import (
    extension_tag, generate_image_thumbnail, get_media_type, is_same_or_under, iter_media_files,
    load_media_info,
)


@pytest.mark.parametrize("path, expected", [
    ("a.mp4", "video"),
    ("a.MKV", "video"),
    ("a.jpg", "image"),
    ("a.JPEG", "image"),
    ("a.txt", None),
    ("noext", None),
])
def test_get_media_type(path, expected):
    assert get_media_type(path) == expected


def test_extension_tag_is_lowercase():
    assert extension_tag(os.path.join("dir", "Movie.MP4")) == "拡張子:mp4"


def test_is_same_or_under(tmp_path):
    base = str(tmp_path / "media")
    assert is_same_or_under(base, base)
    assert is_same_or_under(os.path.join(base, "sub", "a.jpg"), base)
    assert not is_same_or_under(base + "2", base)  # 名前の前方一致だけでは配下とみなさない
    assert not is_same_or_under(str(tmp_path), base)


def test_iter_media_files(media_dir):
    found = sorted(os.path.relpath(p, media_dir) for p in iter_media_files(str(media_dir)))
    assert found == sorted(["a.jpg", "b.png", os.path.join("sub", "c.jpg")])


def test_generate_image_thumbnail_fits_size(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (1600, 1200), "blue").save(path)
    with Image.open(io.BytesIO(generate_image_thumbnail(str(path)))) as thumb:
        assert thumb.format == "JPEG"
        assert thumb.size == (240, 180)


def test_generate_image_thumbnail_broken_file(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image")
    assert generate_image_thumbnail(str(path)) is None


def test_load_media_info_image(media_dir):
    media_type, duration, thumbnail = load_media_info(str(media_dir / "a.jpg"))
    assert media_type == "image"
    assert duration is None
    assert thumbnail
