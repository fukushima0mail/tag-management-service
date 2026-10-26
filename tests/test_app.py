"""メインウィンドウの操作（画面は表示しない）"""
import os

import pytest
from PyQt6.QtCore import Qt

import core.videos as cv
from db.albums import list_albums


def scan_and_wait(window, qapp, directories, tags=None):
    window.start_scan([str(d) for d in directories], tags)
    window.scan_worker.wait()
    qapp.processEvents()  # scan_finished をメインスレッドで処理させる


def select_all(window):
    window.grid.selectAll()


def answer_input(monkeypatch, text: str):
    monkeypatch.setattr(cv.QInputDialog, "getText", staticmethod(lambda *a, **k: (text, True)))


@pytest.fixture
def scanned(window, qapp, media_dir):
    scan_and_wait(window, qapp, [media_dir], ["旅行", "年代:2020"])
    return window


def test_first_launch_creates_default_album(window):
    assert [name for _, name in list_albums()] == ["マイアルバム"]
    assert window.windowTitle().endswith("マイアルバム")
    assert not window.delete_album_action.isEnabled()


def test_scan_updates_list_and_unlocks_buttons(scanned):
    assert len(scanned.videos) == 3
    assert scanned.grid.count() == 3
    assert not scanned.scanning
    assert all(w.isEnabled() for w in scanned.scan_locked_widgets)
    assert scanned.count_label.text() == "3 / 3 件"


def test_filter_by_tag_and_type(scanned):
    items = {item.data(0, Qt.ItemDataRole.UserRole): item for item in scanned._filter_tag_items()}
    items["拡張子:png"].setCheckState(0, Qt.CheckState.Checked)
    assert scanned.grid.count() == 1
    assert scanned.filter_toggle.text() == "絞り込み (1)"

    scanned.clear_tag_filter()
    scanned.type_combo.setCurrentText("動画")
    assert scanned.grid.count() == 0


def test_search_by_filename(scanned):
    scanned.search_edit.setText("B.P")
    assert scanned.grid.count() == 1


def test_sort_by_name_descending(scanned):
    scanned.sort_combo.setCurrentText("名前")
    scanned.order_combo.setCurrentText("降順")
    names = [os.path.basename(scanned.video_by_id[scanned.grid.item(i).data(Qt.ItemDataRole.UserRole)]["file_path"])
             for i in range(scanned.grid.count())]
    assert names == ["c.jpg", "b.png", "a.jpg"]


def test_rating_and_tags_on_selection(scanned):
    select_all(scanned)
    scanned.set_rating_to_selection(7)
    assert all(v["rating"] == 7 for v in scanned.videos)

    select_all(scanned)
    scanned.tag_input.setText("人物 ： 太郎")
    scanned.add_tag_to_selection()
    assert "人物:太郎" in scanned._all_tags()

    select_all(scanned)
    for i in range(scanned.detail_tags.count()):
        item = scanned.detail_tags.item(i)
        item.setSelected(item.data(Qt.ItemDataRole.UserRole) in ("旅行", "人物:太郎"))
    scanned.remove_tags_from_selection()
    assert not {"旅行", "人物:太郎"} & scanned._all_tags()


def test_rename_group_keeps_collapsed_state(scanned, monkeypatch):
    scanned._save_collapsed_groups({"年代"})
    answer_input(monkeypatch, "西暦")
    scanned.rename_group("年代")
    assert "西暦:2020" in scanned._all_tags()
    assert scanned._collapsed_groups() == {"西暦"}


def test_ungroup(scanned):
    scanned.ungroup("年代")
    assert "2020" in scanned._all_tags()


def test_rename_selection_renames_real_file(scanned, monkeypatch, media_dir):
    scanned.sort_combo.setCurrentText("名前")
    scanned.order_combo.setCurrentText("昇順")
    scanned.grid.item(0).setSelected(True)  # a.jpg
    answer_input(monkeypatch, "renamed")
    scanned.rename_selection()
    assert (media_dir / "renamed.jpg").exists()
    assert not (media_dir / "a.jpg").exists()
    assert any(v["file_path"].endswith("renamed.jpg") for v in scanned.videos)


def test_exclude_selection_is_not_rescanned(scanned, qapp, media_dir):
    scanned.grid.item(0).setSelected(True)
    scanned.exclude_selection()
    assert len(scanned.videos) == 2
    scan_and_wait(scanned, qapp, [media_dir])
    assert len(scanned.videos) == 2


def test_albums_create_switch_rename_delete(scanned, monkeypatch):
    answer_input(monkeypatch, "別アルバム")
    scanned.create_album()
    assert scanned.videos == []
    assert scanned.windowTitle().endswith("別アルバム")
    assert scanned.delete_album_action.isEnabled()

    scanned.on_album_selected(0)
    assert len(scanned.videos) == 3

    answer_input(monkeypatch, "メイン")
    scanned.rename_album()
    assert scanned.album_combo.itemText(0) == "メイン"

    # テストではごみ箱を使わず、直接削除する
    monkeypatch.setattr(cv.QFile, "moveToTrash", staticmethod(lambda p: (os.remove(p) is None, p)))
    scanned.delete_album()
    assert [name for _, name in list_albums()] == ["別アルバム"]
    assert scanned.videos == []
    assert not scanned.delete_album_action.isEnabled()


def test_reopens_last_album(window, tmp_path, monkeypatch):
    answer_input(monkeypatch, "2つ目")
    window.create_album()
    window.close()
    reopened = cv.VideoTagApp()
    assert reopened.windowTitle().endswith("2つ目")
    reopened.close()


def test_scan_message_includes_moved(scanned, qapp, media_dir):
    (media_dir / "a.jpg").rename(media_dir / "moved.jpg")
    scan_and_wait(scanned, qapp, [media_dir])
    assert "移動 1" in scanned.statusBar().currentMessage()
    assert len(scanned.videos) == 3


def test_cancel_scan(window, qapp, media_dir, monkeypatch):
    # 開始直後に中止ボタンを押した状態にする
    monkeypatch.setattr(cv.ScanWorker, "isInterruptionRequested", lambda self: True)
    window.start_scan([str(media_dir)])
    assert window.cancel_scan_button.isVisibleTo(window)
    window.cancel_scan()
    window.scan_worker.wait()
    qapp.processEvents()
    assert not window.scanning
    assert not window.cancel_scan_button.isVisibleTo(window)
    assert window.statusBar().currentMessage().startswith("スキャンを中止しました")


def test_copy_selected_paths(scanned, qapp):
    select_all(scanned)
    scanned.copy_selected_paths()
    assert sorted(qapp.clipboard().text().split("\n")) == sorted(v["file_path"] for v in scanned.videos)


def test_reveal_missing_file_warns(scanned, monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(cv.QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    scanned.reveal_file(str(tmp_path / "no_such_dir" / "x.jpg"))
    assert warnings


def test_export_import_via_menu(scanned, monkeypatch, tmp_path):
    out = str(tmp_path / "export.json")
    monkeypatch.setattr(cv.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    monkeypatch.setattr(cv.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (out, "")))
    infos = []
    monkeypatch.setattr(cv.QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a)))

    select_all(scanned)
    scanned.set_rating_to_selection(6)
    scanned.export_tags()

    select_all(scanned)
    scanned.set_rating_to_selection(0)
    scanned.import_tags()
    assert all(v["rating"] == 6 for v in scanned.videos)
    assert "3 件" in infos[0][2]
