import os

import pytest

from core.scanner import ScanWorker
from db.exclusions import add_excluded_paths
from db.tags import add_tag
from db.videos import get_all_videos


def scan(directories=(), tags=None, files=None, tag_new_only=False) -> tuple[int, int, int]:
    """スキャンをスレッドを使わずに実行し、(追加, 更新, 削除) を返す"""
    worker = ScanWorker([str(d) for d in directories], tags, [str(f) for f in files or []], tag_new_only)
    result = []
    worker.scan_finished.connect(lambda *counts: result.append(counts))
    worker.run()
    return result[0]


def videos_by_name() -> dict[str, dict]:
    return {os.path.basename(v["file_path"]): v for v in get_all_videos()}


@pytest.fixture(autouse=True)
def _qapp(qapp):
    pass


def test_scan_registers_media_with_extension_and_given_tags(album, media_dir):
    assert scan([media_dir], tags=["旅行"]) == (3, 0, 0)
    videos = videos_by_name()
    assert sorted(videos) == ["a.jpg", "b.png", "c.jpg"]
    assert set(videos["b.png"]["tags"]) == {"旅行", "拡張子:png"}
    assert videos["a.jpg"]["media_type"] == "image"


def test_rescan_skips_unchanged_and_updates_modified(album, media_dir):
    scan([media_dir])
    assert scan([media_dir]) == (0, 0, 0)

    path = media_dir / "a.jpg"
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert scan([media_dir]) == (0, 1, 0)


def test_rescan_removes_deleted_files(album, media_dir):
    scan([media_dir])
    (media_dir / "b.png").unlink()
    assert scan([media_dir]) == (0, 0, 1)
    assert "b.png" not in videos_by_name()


def test_missing_directory_does_not_remove_files(album, media_dir, tmp_path):
    scan([media_dir])
    media_dir.rename(tmp_path / "unplugged")  # 外付けドライブ未接続など
    assert scan([media_dir]) == (0, 0, 0)
    assert len(get_all_videos()) == 3


def test_tag_new_only_skips_registered_files(album, media_dir):
    scan(files=[media_dir / "a.jpg"])
    scan([media_dir], tags=["新規"], tag_new_only=True)
    videos = videos_by_name()
    assert "新規" not in videos["a.jpg"]["tags"]
    assert "新規" in videos["b.png"]["tags"]


def test_tags_added_to_unchanged_files_by_default(album, media_dir):
    scan([media_dir])
    scan([media_dir], tags=["後から"])
    assert all("後から" in v["tags"] for v in get_all_videos())


def test_excluded_paths_are_skipped(album, media_dir):
    add_excluded_paths([str(media_dir / "a.jpg"), str(media_dir / "sub")])
    scan([media_dir])
    assert sorted(videos_by_name()) == ["b.png"]


def test_explicit_directory_overrides_excluded_parent(album, media_dir):
    add_excluded_paths([str(media_dir)])
    scan([media_dir / "sub"])
    assert sorted(videos_by_name()) == ["c.jpg"]


def test_scan_individual_files(album, media_dir):
    assert scan(files=[media_dir / "b.png"], tags=["個別"]) == (1, 0, 0)
    assert set(videos_by_name()["b.png"]["tags"]) == {"個別", "拡張子:png"}


def test_scan_keeps_existing_tags(album, media_dir):
    scan([media_dir])
    video_id = videos_by_name()["a.jpg"]["id"]
    add_tag([video_id], "手動")
    scan([media_dir])
    assert "手動" in videos_by_name()["a.jpg"]["tags"]
