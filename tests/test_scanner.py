import os

import pytest

from core.scanner import ScanWorker
from db.exclusions import add_excluded_paths
from db.tags import add_tag
from db.videos import get_all_videos


def scan(directories=(), tags=None, files=None, tag_new_only=False) -> tuple[int, int, int, int]:
    """スキャンをスレッドを使わずに実行し、(追加, 更新, 削除, 移動) を返す"""
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
    assert scan([media_dir], tags=["旅行"]) == (3, 0, 0, 0)
    videos = videos_by_name()
    assert sorted(videos) == ["a.jpg", "b.png", "c.jpg"]
    assert set(videos["b.png"]["tags"]) == {"旅行", "拡張子:png"}
    assert videos["a.jpg"]["media_type"] == "image"


def test_rescan_skips_unchanged_and_updates_modified(album, media_dir):
    scan([media_dir])
    assert scan([media_dir]) == (0, 0, 0, 0)

    path = media_dir / "a.jpg"
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert scan([media_dir]) == (0, 1, 0, 0)


def test_rescan_removes_deleted_files(album, media_dir):
    scan([media_dir])
    (media_dir / "b.png").unlink()
    assert scan([media_dir]) == (0, 0, 1, 0)
    assert "b.png" not in videos_by_name()


def test_missing_directory_does_not_remove_files(album, media_dir, tmp_path):
    scan([media_dir])
    media_dir.rename(tmp_path / "unplugged")  # 外付けドライブ未接続など
    assert scan([media_dir]) == (0, 0, 0, 0)
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
    assert scan(files=[media_dir / "b.png"], tags=["個別"]) == (1, 0, 0, 0)
    assert set(videos_by_name()["b.png"]["tags"]) == {"個別", "拡張子:png"}


def test_scan_keeps_existing_tags(album, media_dir):
    scan([media_dir])
    video_id = videos_by_name()["a.jpg"]["id"]
    add_tag([video_id], "手動")
    scan([media_dir])
    assert "手動" in videos_by_name()["a.jpg"]["tags"]


# ---------------------------------------------------------------- 移動・リネーム
def _tag_and_rate(name: str, tag: str, rating: int):
    from db.videos import set_rating
    video = videos_by_name()[name]
    add_tag([video["id"]], tag)
    set_rating([video["id"]], rating)
    return video["id"]


def test_renamed_file_keeps_tags_and_rating(album, media_dir):
    scan([media_dir])
    video_id = _tag_and_rate("a.jpg", "お気に入り", 9)
    (media_dir / "a.jpg").rename(media_dir / "renamed.jpg")

    assert scan([media_dir]) == (0, 0, 0, 1)
    video = videos_by_name()["renamed.jpg"]
    assert video["id"] == video_id
    assert "お気に入り" in video["tags"] and video["rating"] == 9
    assert "a.jpg" not in videos_by_name()


def test_moved_to_other_folder_keeps_tags(album, media_dir, tmp_path):
    scan([media_dir])
    _tag_and_rate("b.png", "移動前", 3)
    other = tmp_path / "other"
    other.mkdir()
    (media_dir / "b.png").rename(other / "b.png")

    # 移動先のフォルダだけをスキャンしても引き継がれる
    assert scan([other]) == (0, 0, 0, 1)
    assert videos_by_name()["b.png"]["file_path"] == str(other / "b.png")
    assert "移動前" in videos_by_name()["b.png"]["tags"]


def test_renamed_folder_keeps_tags(album, media_dir, tmp_path):
    scan([media_dir])
    _tag_and_rate("c.jpg", "サブ", 5)
    (media_dir / "sub").rename(media_dir / "sub2")
    assert scan([media_dir]) == (0, 0, 0, 1)
    assert "サブ" in videos_by_name()["c.jpg"]["tags"]


def test_copied_file_is_registered_as_new(album, media_dir, tmp_path):
    """元ファイルが残っている場合（コピー）は、別ファイルとして新規登録する"""
    import shutil
    scan([media_dir])
    _tag_and_rate("a.jpg", "元", 1)
    shutil.copy2(media_dir / "a.jpg", media_dir / "copy.jpg")  # 更新日時もコピーされる
    assert scan([media_dir]) == (1, 0, 0, 0)
    assert "元" not in videos_by_name()["copy.jpg"]["tags"]


def test_changed_size_is_not_treated_as_move(album, media_dir):
    from PIL import Image
    scan([media_dir])
    _tag_and_rate("a.jpg", "別物", 1)
    (media_dir / "a.jpg").unlink()
    Image.new("RGB", (200, 100), "green").save(media_dir / "new.jpg")
    assert scan([media_dir]) == (1, 0, 1, 0)
    assert "別物" not in videos_by_name()["new.jpg"]["tags"]


# ---------------------------------------------------------------- 中止
def test_interrupted_scan_does_not_remove_files(album, media_dir):
    scan([media_dir])
    (media_dir / "b.png").unlink()
    worker = ScanWorker([str(media_dir)])
    result = []
    worker.scan_finished.connect(lambda *counts: result.append(counts))
    worker.isInterruptionRequested = lambda: True  # 開始直後に中止された状態
    worker.run()
    assert result == [(0, 0, 0, 0)]
    assert "b.png" in videos_by_name()  # 中止時は削除しない


# ---------------------------------------------------------------- 並列化・まとめて書き込み
@pytest.fixture
def many_media(tmp_path):
    from PIL import Image
    root = tmp_path / "many"
    root.mkdir()
    for i in range(60):
        Image.new("RGB", (64 + i, 48), (i * 4, 0, 0)).save(root / f"{i:03}.jpg")
    return root


def test_parallel_scan_registers_everything(album, many_media, monkeypatch):
    import core.scanner as scanner
    monkeypatch.setattr(scanner, "WRITE_BATCH_SIZE", 7)  # 端数のある件数でまとめて書き込む
    progress = []
    worker = ScanWorker([str(many_media)], ["一括"])
    result = []
    worker.progress.connect(lambda done, total, path: progress.append((done, total)))
    worker.scan_finished.connect(lambda *counts: result.append(counts))
    worker.run()

    assert result == [(60, 0, 0, 0)]
    videos = get_all_videos()
    assert len(videos) == 60
    assert all({"一括", "拡張子:jpg"} <= set(v["tags"]) for v in videos)
    assert all(v["file_size"] for v in videos)
    assert [d for d, _ in progress] == list(range(1, 61))  # 進捗は1件ずつ進む
    assert {t for _, t in progress} == {60}


def test_interrupt_during_thumbnail_creation_keeps_finished(album, many_media):
    worker = ScanWorker([str(many_media)])
    result = []
    worker.scan_finished.connect(lambda *counts: result.append(counts))
    # 10 件処理したところで中止ボタンが押された状態にする
    worker.progress.connect(lambda done, total, path: done >= 10 and setattr(worker, "_stop", True))
    worker.isInterruptionRequested = lambda: getattr(worker, "_stop", False)
    worker.run()

    added = result[0][0]
    assert 10 <= added < 60                      # 作成済みの分は登録される
    assert len(get_all_videos()) == added
    assert scan([many_media]) == (60 - added, 0, 0, 0)  # 続きは次のスキャンで登録される
