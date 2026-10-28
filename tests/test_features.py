"""絞り込みの強化・タグの一括削除・ドラッグ＆ドロップ・自動バックアップ・重複ファイルの検出"""
import os
import time

import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

import core.videos as cv
from core.duplicates import SAMPLE_SIZE, DuplicateWorker, keep_candidate, sample_hash
from db.backup import KEEP_BACKUPS, backup_album, backup_daily, list_backups, restore_album
from db.connection import get_db_path
from db.tags import add_tag, delete_tags


def scan_and_wait(window, qapp, directories, tags=None, files=None):
    window.start_scan([str(d) for d in directories], tags, [str(f) for f in files or []])
    window.scan_worker.wait()
    qapp.processEvents()


def shown_names(window) -> list[str]:
    return sorted(os.path.basename(window.video_by_id[i]["file_path"]) for i in window.grid.ids())


def video(window, name: str) -> dict:
    return next(v for v in window.videos if os.path.basename(v["file_path"]) == name)


@pytest.fixture
def scanned(window, qapp, media_dir):
    cv.set_directories([str(media_dir)])
    scan_and_wait(window, qapp, [media_dir])
    return window


# ---------------------------------------------------------------- 絞り込みの強化
def test_rating_filter(scanned):
    cv.set_rating([video(scanned, "a.jpg")["id"]], 8)
    cv.set_rating([video(scanned, "b.png")["id"]], 3)
    scanned.reload_from_db()

    scanned.rating_filter_combo.setCurrentText("★5 以上")
    assert shown_names(scanned) == ["a.jpg"]
    scanned.rating_filter_combo.setCurrentText("★3 以上")
    assert shown_names(scanned) == ["a.jpg", "b.png"]
    scanned.rating_filter_combo.setCurrentText("未評価のみ")
    assert shown_names(scanned) == ["c.jpg"]
    assert scanned.filter_toggle.text() == "絞り込み (1)"


def test_untagged_filter_ignores_extension_tags(scanned):
    add_tag([video(scanned, "a.jpg")["id"]], "旅行")
    scanned.reload_from_db()
    scanned.untagged_check.setChecked(True)
    assert shown_names(scanned) == ["b.png", "c.jpg"]  # 拡張子タグだけのファイルは「タグなし」


def test_exclude_tag(scanned):
    add_tag([video(scanned, "a.jpg")["id"]], "除外したい")
    scanned.reload_from_db()
    scanned.set_tag_excluded("除外したい", True)
    assert shown_names(scanned) == ["b.png", "c.jpg"]
    scanned.set_tag_excluded("拡張子:png", True)
    assert shown_names(scanned) == ["c.jpg"]
    assert scanned.filter_toggle.text() == "絞り込み (2)"

    # 除外中のタグにチェックを入れると「含む」条件に変わる
    item = next(i for i in scanned._filter_tag_items() if i.data(0, Qt.ItemDataRole.UserRole) == "除外したい")
    item.setCheckState(0, Qt.CheckState.Checked)
    assert "除外したい" not in scanned.excluded_tags
    assert shown_names(scanned) == ["a.jpg"]


def test_exclusion_survives_reload_and_rename(scanned, monkeypatch):
    add_tag([video(scanned, "a.jpg")["id"]], "old")
    scanned.reload_from_db()
    scanned.set_tag_excluded("old", True)
    scanned.reload_from_db()
    assert shown_names(scanned) == ["b.png", "c.jpg"]
    monkeypatch.setattr(cv.QInputDialog, "getText", staticmethod(lambda *a, **k: ("new", True)))
    scanned.rename_tag("old")
    assert scanned.excluded_tags == {"new"}
    assert shown_names(scanned) == ["b.png", "c.jpg"]


def test_clear_filter_resets_everything(scanned):
    scanned.set_tag_excluded("拡張子:png", True)
    scanned.rating_filter_combo.setCurrentText("未評価のみ")
    scanned.untagged_check.setChecked(True)
    scanned.clear_tag_filter()
    assert shown_names(scanned) == ["a.jpg", "b.png", "c.jpg"]
    assert not scanned.excluded_tags
    assert scanned.filter_toggle.text() == "絞り込み"


# ---------------------------------------------------------------- タグの一括削除
def test_delete_tags_db(album):
    from db.videos import add_video, get_all_videos
    a = add_video("C:/m/a.jpg", "image", None, None, 1, 1.0)
    b = add_video("C:/m/b.jpg", "image", None, None, 1, 1.0)
    add_tag([a, b], "x")
    add_tag([a], "y")
    assert delete_tags(["x", "存在しない"]) == 2
    assert {v["id"]: v["tags"] for v in get_all_videos()} == {a: ["y"], b: []}


def test_delete_tag_from_menu(scanned):
    ids = [v["id"] for v in scanned.videos]
    add_tag(ids, "消す")
    scanned.reload_from_db()
    scanned.set_tag_excluded("消す", True)
    scanned.delete_tags(["消す"])
    assert "消す" not in scanned._all_tags()
    assert not scanned.excluded_tags
    assert len(scanned.videos) == 3                       # ファイルは残る
    assert "3 件のファイルから外しました" in scanned.statusBar().currentMessage()


def test_delete_group_tags(scanned):
    add_tag([video(scanned, "a.jpg")["id"]], "年代:2020")
    add_tag([video(scanned, "b.png")["id"]], "年代:2021")
    scanned.reload_from_db()
    scanned.delete_tags(scanned._group_tags("年代"), "年代")
    assert not any(t.startswith("年代:") for t in scanned._all_tags())


def test_delete_tag_cancelled(scanned, monkeypatch):
    monkeypatch.setattr(cv.QMessageBox, "question", staticmethod(lambda *a, **k: cv.QMessageBox.StandardButton.No))
    scanned.delete_tags(["拡張子:png"])
    assert "拡張子:png" in scanned._all_tags()


# ---------------------------------------------------------------- ドラッグ＆ドロップ
def _drop(window, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(enter)
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier)
    window.dropEvent(drop)
    return enter.isAccepted()


@pytest.fixture
def accept_add_dialog(monkeypatch):
    """追加時のタグ入力ダイアログを、指定したタグで OK したことにする"""
    def use(tags=()):
        class Dialog:
            def tags(self):
                return list(tags)

            def new_only(self):
                return False
        monkeypatch.setattr(cv.VideoTagApp, "_ask_add_tags", lambda self, title, target: Dialog())
    return use


def test_drop_folder_registers_and_scans(window, qapp, media_dir, accept_add_dialog):
    accept_add_dialog(["ドロップ"])
    assert _drop(window, [media_dir])
    window.scan_worker.wait()
    qapp.processEvents()
    assert cv.get_directories() == [str(media_dir)]
    assert len(window.videos) == 3
    assert all("ドロップ" in v["tags"] for v in window.videos)


def test_drop_files_and_skip_non_media(window, qapp, media_dir, accept_add_dialog):
    accept_add_dialog()
    _drop(window, [media_dir / "a.jpg", media_dir / "memo.txt"])
    window.scan_worker.wait()
    qapp.processEvents()
    assert [os.path.basename(v["file_path"]) for v in window.videos] == ["a.jpg"]
    assert cv.get_directories() == []  # ファイルだけなら登録フォルダは増えない


def test_drop_only_non_media(window, media_dir, accept_add_dialog):
    accept_add_dialog()
    _drop(window, [media_dir / "memo.txt"])
    assert window.scan_worker is None
    assert "追加できる動画・画像がありませんでした" in window.statusBar().currentMessage()


def test_drop_rejected_while_scanning(window, media_dir):
    window.scanning = True
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(media_dir))])
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    enter.ignore()
    window.dragEnterEvent(enter)
    assert not enter.isAccepted()
    window.scanning = False


# ---------------------------------------------------------------- 自動バックアップ
def test_backup_keeps_limited_generations(album):
    for _ in range(KEEP_BACKUPS + 3):
        backup_album(album, "テスト")
    backups = list_backups(album)
    assert len(backups) == KEEP_BACKUPS
    assert all(os.path.basename(p).endswith("_テスト.db") for p in backups)


def test_daily_backup_once_per_day(album):
    assert backup_daily(album)
    assert backup_daily(album) is None
    assert len(list_backups(album)) == 1


def test_restore(album):
    from db.videos import add_video, get_all_videos
    add_video("C:/m/a.jpg", "image", None, None, 1, 1.0)
    saved = backup_album(album)
    add_video("C:/m/b.jpg", "image", None, None, 1, 1.0)
    time.sleep(0.01)
    before = restore_album(album, saved)
    assert [v["file_path"] for v in get_all_videos()] == ["C:/m/a.jpg"]
    assert "復元前" in os.path.basename(before)


def test_window_backs_up_on_open_and_before_bulk_changes(scanned):
    names = [os.path.basename(p) for p in list_backups(get_db_path())]
    assert any(n.endswith("_自動.db") for n in names)    # アルバムを開いたとき
    scanned.delete_tags(["拡張子:png"])
    names = [os.path.basename(p) for p in list_backups(get_db_path())]
    assert any(n.endswith("_タグを削除.db") for n in names)


def test_restore_from_menu(scanned, monkeypatch):
    saved = backup_album(get_db_path(), "手動")
    scanned.delete_tags(["拡張子:png"])
    monkeypatch.setattr(cv.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (saved, "")))
    scanned.restore_backup()
    assert "拡張子:png" in scanned._all_tags()


# ---------------------------------------------------------------- 重複ファイルの検出
def test_sample_hash(tmp_path):
    small_a, small_b = tmp_path / "a", tmp_path / "b"
    small_a.write_bytes(b"x" * 100)
    small_b.write_bytes(b"x" * 99 + b"y")
    assert sample_hash(str(small_a), 100) != sample_hash(str(small_b), 100)

    # 大きなファイルは先頭・中央・末尾だけを比べる
    size = SAMPLE_SIZE * 5
    big_a, big_b, big_c = tmp_path / "A", tmp_path / "B", tmp_path / "C"
    data = bytearray(size)
    big_a.write_bytes(bytes(data))
    data[SAMPLE_SIZE + 10] = 1   # 比べない範囲だけ違う
    big_b.write_bytes(bytes(data))
    data[-1] = 1                 # 末尾が違う
    big_c.write_bytes(bytes(data))
    assert sample_hash(str(big_a), size) == sample_hash(str(big_b), size)
    assert sample_hash(str(big_a), size) != sample_hash(str(big_c), size)


def test_keep_candidate():
    group = [
        {"id": 1, "rating": 0, "tags": ["a"], "added_at": "2020"},
        {"id": 2, "rating": 5, "tags": [], "added_at": "2021"},
        {"id": 3, "rating": 5, "tags": ["a", "b"], "added_at": "2022"},
    ]
    assert keep_candidate(group)["id"] == 3


def _find(window, qapp) -> list[list[dict]]:
    worker = DuplicateWorker(window.videos)
    groups = []
    worker.found.connect(groups.extend)
    worker.run()
    return groups


def test_duplicate_worker_finds_same_content(scanned, qapp, media_dir):
    # media_dir の a.jpg と sub/c.jpg は同じ内容
    groups = _find(scanned, qapp)
    assert [sorted(os.path.basename(v["file_path"]) for v in g) for g in groups] == [["a.jpg", "c.jpg"]]


def test_duplicate_worker_skips_missing_files(scanned, qapp, media_dir):
    (media_dir / "sub" / "c.jpg").unlink()
    assert _find(scanned, qapp) == []


def test_duplicates_dialog_show_and_select(scanned, qapp):
    from core.duplicate_dialog import DuplicateDialog
    groups = _find(scanned, qapp)
    a = next(v for v in groups[0] if v["file_path"].endswith("a.jpg"))
    cv.set_rating([a["id"]], 9)  # a.jpg を残す候補にする
    scanned.reload_from_db()
    groups = _find(scanned, qapp)

    dialog = DuplicateDialog(scanned, groups)
    dialog.show_in_list()
    assert shown_names(scanned) == ["a.jpg", "c.jpg"]
    assert scanned.only_groups_button.isVisibleTo(scanned)

    dialog.select_extras()
    assert [os.path.basename(v["file_path"]) for v in scanned._selected_videos()] == ["c.jpg"]

    scanned.clear_only_groups()
    assert len(scanned.grid.ids()) == 3
    dialog.close()


def test_find_duplicates_none(window, qapp, monkeypatch):
    infos = []
    monkeypatch.setattr(cv.QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a[2])))
    window.on_duplicates_found([])
    assert "見つかりませんでした" in infos[0]


def test_find_duplicates_from_menu(scanned, qapp):
    scanned.find_duplicates()
    scanned.duplicate_worker.wait()
    for _ in range(5):
        qapp.processEvents()
    assert scanned.duplicate_dialog is not None
    assert scanned.duplicate_dialog.tree.topLevelItemCount() == 1
    scanned.duplicate_dialog.close()
