"""サムネイルの作り直し（1件）と、動画の場面選び"""
import io
import shutil
import subprocess

import pytest
from PIL import Image
from PyQt6.QtWidgets import QDialog

from core import media
from core.scanner import ScanWorker
from core.thumbnail_dialog import ThumbnailSceneDialog, format_position
from db.albums import set_thumbnail_width
from db.videos import get_thumbnail_position, get_thumbnails

has_ffmpeg = media.ffmpeg is not None and shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg が必要")


def _color(jpeg: bytes) -> tuple[int, int, int]:
    """サムネイル中央の色"""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    return img.getpixel((img.width // 2, img.height // 2))


def _is_blue(jpeg: bytes) -> bool:
    r, g, b = _color(jpeg)
    return b > 150 and r < 100


@pytest.fixture
def two_color_video(tmp_path):
    """前半4秒が赤、後半2秒が青の動画（既定の位置 3秒は赤、5秒は青）"""
    path = tmp_path / "media" / "clip.mp4"
    path.parent.mkdir(exist_ok=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=160x90:d=4:r=10",
        "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=2:r=10",
        "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p", str(path),
    ], check=True)
    return path


def _scan(directory):
    worker = ScanWorker([str(directory)])
    worker.run()


def test_format_position():
    assert format_position(0) == "0:00.0"
    assert format_position(83.44) == "1:23.4"
    assert format_position(3723.5) == "1:02:03.5"


@needs_ffmpeg
def test_generate_video_thumbnail_at_position(two_color_video):
    duration = media.get_video_duration(str(two_color_video))
    assert not _is_blue(media.generate_video_thumbnail(str(two_color_video), duration, 160))
    assert _is_blue(media.generate_video_thumbnail(str(two_color_video), duration, 160, position=5.0))
    # 再生時間より後ろを指定しても、最後のあたりのフレームが取れる
    assert _is_blue(media.generate_video_thumbnail(str(two_color_video), duration, 160, position=60.0))


@needs_ffmpeg
def test_scene_dialog_accept(qapp, two_color_video):
    duration = media.get_video_duration(str(two_color_video))
    dialog = ThumbnailSceneDialog(str(two_color_video), duration, 160)
    try:
        assert dialog.position == media.default_thumbnail_position(duration)
        dialog.set_position(5.0)
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.position == 5.0
        assert _is_blue(dialog.thumbnail)
    finally:
        dialog.worker.stop()


@needs_ffmpeg
def test_scene_dialog_candidates_and_preview(qapp, two_color_video):
    """候補のフレームとプレビューがバックグラウンドで作られる"""
    duration = media.get_video_duration(str(two_color_video))
    dialog = ThumbnailSceneDialog(str(two_color_video), duration, 160)
    try:
        dialog.worker.request_preview(5.0)
        for _ in range(200):
            qapp.processEvents()
            if all(not b.icon().isNull() for b, _ in dialog.candidate_buttons) and 5.0 in dialog._frames:
                break
            dialog.worker.wait(20)
        assert all(not b.icon().isNull() for b, _ in dialog.candidate_buttons)
        assert _is_blue(dialog._frames[5.0])
    finally:
        dialog.reject()
    assert dialog.worker.isFinished()


@needs_ffmpeg
def test_regenerate_video_thumbnail_keeps_position_on_rescan(window, two_color_video, monkeypatch):
    import core.videos as cv

    _scan(two_color_video.parent)
    window.reload_from_db()
    video = window.videos[0]
    assert not _is_blue(get_thumbnails([video["id"]])[video["id"]])

    def choose_blue(dialog):
        dialog.set_position(5.0)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(cv.ThumbnailSceneDialog, "exec", choose_blue)
    window.regenerate_thumbnail(video)
    assert get_thumbnail_position(video["id"]) == 5.0
    assert _is_blue(get_thumbnails([video["id"]])[video["id"]])

    # サムネイルサイズを変えて再スキャンしても、選んだ場面のまま作り直される
    set_thumbnail_width(240)
    _scan(two_color_video.parent)
    thumbnail = get_thumbnails([video["id"]])[video["id"]]
    assert Image.open(io.BytesIO(thumbnail)).width == 160  # 元の動画より大きくはしない
    assert _is_blue(thumbnail)


@needs_ffmpeg
def test_regenerate_cancel_keeps_thumbnail(window, two_color_video, monkeypatch):
    import core.videos as cv

    _scan(two_color_video.parent)
    window.reload_from_db()
    video = window.videos[0]
    before = get_thumbnails([video["id"]])[video["id"]]
    monkeypatch.setattr(cv.ThumbnailSceneDialog, "exec", lambda d: (d.reject(), d.result())[1])
    window.regenerate_thumbnail(video)
    assert get_thumbnails([video["id"]])[video["id"]] == before
    assert get_thumbnail_position(video["id"]) is None


def test_regenerate_image_thumbnail(window, media_dir):
    _scan(media_dir)
    window.reload_from_db()
    video = next(v for v in window.videos if v["file_path"].endswith("a.jpg"))
    assert _color(get_thumbnails([video["id"]])[video["id"]])[0] > 200  # 赤

    Image.new("RGB", (64, 48), "blue").save(video["file_path"])
    window.grid.thumbnail_model.cache.put(video["id"], None)
    window.regenerate_thumbnail(video)
    assert _is_blue(get_thumbnails([video["id"]])[video["id"]])
    assert video["id"] not in window.grid.thumbnail_model.cache  # 一覧のサムネイルも読み直す
