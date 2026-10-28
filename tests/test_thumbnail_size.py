"""アルバムごとのサムネイルサイズ"""
import io

import pytest
from PIL import Image
from PyQt6.QtWidgets import QMessageBox

import core.videos as cv
from core.media import DEFAULT_THUMBNAIL_WIDTH, generate_image_thumbnail, thumbnail_box
from core.scanner import ScanWorker
from db.albums import create_album, get_thumbnail_width, set_thumbnail_width
from db.connection import set_db_path
from db.init import init_table
from db.videos import add_video, get_all_videos, get_outdated_thumbnail_ids, get_thumbnails


@pytest.fixture(autouse=True)
def _qapp(qapp):
    pass


def scan(directories) -> tuple[int, int, int, int]:
    worker = ScanWorker([str(d) for d in directories])
    result = []
    worker.scan_finished.connect(lambda *counts: result.append(counts))
    worker.run()
    return result[0]


def thumbnail_widths() -> list[int]:
    thumbnails = get_thumbnails([v["id"] for v in get_all_videos()])
    return sorted(Image.open(io.BytesIO(data)).width for data in thumbnails.values())


@pytest.fixture
def wide_media(tmp_path):
    """横長の画像（幅でサムネイルの大きさが決まる）"""
    root = tmp_path / "wide"
    root.mkdir()
    for name in ("a.jpg", "b.png"):
        Image.new("RGB", (1920, 1080), "red").save(root / name)
    return root


def test_thumbnail_box_is_16_9():
    assert thumbnail_box(320) == (320, 180)
    assert thumbnail_box(640) == (640, 360)


def test_generate_image_thumbnail_with_width(wide_media):
    data = generate_image_thumbnail(str(wide_media / "a.jpg"), 480)
    assert Image.open(io.BytesIO(data)).size == (480, 270)


def test_default_width(album):
    assert get_thumbnail_width() == DEFAULT_THUMBNAIL_WIDTH


def test_width_is_per_album(album):
    set_thumbnail_width(640)
    other = create_album("別")
    set_db_path(other)
    init_table()
    assert get_thumbnail_width() == DEFAULT_THUMBNAIL_WIDTH
    set_db_path(album)
    assert get_thumbnail_width() == 640


def test_outdated_ids(album):
    old = add_video("C:/m/old.jpg", "image", None, None, 1, 1.0)  # 既定の幅で作成
    big = add_video("C:/m/big.jpg", "image", None, None, 1, 1.0, thumbnail_width=640)
    assert get_outdated_thumbnail_ids(DEFAULT_THUMBNAIL_WIDTH) == {big}
    assert get_outdated_thumbnail_ids(640) == {old}


def test_rescan_regenerates_thumbnails_after_size_change(album, wide_media):
    assert scan([wide_media]) == (2, 0, 0, 0)
    assert thumbnail_widths() == [320, 320]

    set_thumbnail_width(640)
    assert scan([wide_media]) == (0, 2, 0, 0)  # 変更のないファイルもサムネイルを作り直す
    assert thumbnail_widths() == [640, 640]
    assert scan([wide_media]) == (0, 0, 0, 0)  # 作り直した後は再処理しない


def test_regenerating_keeps_tags_and_rating(album, wide_media):
    from db.tags import add_tag
    from db.videos import set_rating
    scan([wide_media])
    ids = [v["id"] for v in get_all_videos()]
    add_tag(ids, "残す")
    set_rating(ids, 4)

    set_thumbnail_width(240)
    scan([wide_media])
    assert all("残す" in v["tags"] and v["rating"] == 4 for v in get_all_videos())
    assert [v["id"] for v in get_all_videos()] == ids


def test_change_thumbnail_size_from_menu(window, qapp, wide_media, monkeypatch):
    cv.set_directories([str(wide_media)])
    window.rescan_all()
    window.scan_worker.wait()
    qapp.processEvents()

    monkeypatch.setattr(cv.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("大 (480px)", True)))
    window.change_thumbnail_size()  # 確認ダイアログは「はい」→ すぐに再スキャン
    window.scan_worker.wait()
    qapp.processEvents()
    assert get_thumbnail_width() == 480
    assert thumbnail_widths() == [480, 480]
    assert "更新 2" in window.statusBar().currentMessage()


def test_change_thumbnail_size_later(window, monkeypatch):
    monkeypatch.setattr(cv.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("小 (240px)", True)))
    monkeypatch.setattr(cv.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    window.change_thumbnail_size()
    assert get_thumbnail_width() == 240
    assert not window.scanning
    assert "次回の再スキャン" in window.statusBar().currentMessage()
