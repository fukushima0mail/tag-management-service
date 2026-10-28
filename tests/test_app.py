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
    cv.set_directories([str(media_dir)])
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
    names = [os.path.basename(scanned.video_by_id[i]["file_path"]) for i in scanned.grid.ids()]
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
    scanned.grid.select_rows([0])  # a.jpg
    answer_input(monkeypatch, "renamed")
    scanned.rename_selection()
    assert (media_dir / "renamed.jpg").exists()
    assert not (media_dir / "a.jpg").exists()
    assert any(v["file_path"].endswith("renamed.jpg") for v in scanned.videos)


def test_exclude_selection_is_not_rescanned(scanned, qapp, media_dir):
    scanned.grid.select_rows([0])
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


# ---------------------------------------------------------------- フォルダの除外・一覧
def test_excluded_subfolder_is_not_rescanned(scanned, qapp, media_dir):
    assert scanned.exclude_directory_path(str(media_dir / "sub"))
    assert len(scanned.videos) == 2
    scanned.rescan_all()
    scanned.scan_worker.wait()
    qapp.processEvents()
    assert sorted(os.path.basename(v["file_path"]) for v in scanned.videos) == ["a.jpg", "b.png"]


def test_excluded_registered_folder_is_listed_and_not_rescanned(window, qapp, media_dir, monkeypatch):
    cv.set_directories([str(media_dir)])
    window.rescan_all()
    window.scan_worker.wait()
    qapp.processEvents()
    assert len(window.videos) == 3

    assert window.exclude_directory_path(str(media_dir))
    assert cv.get_directories() == []
    assert cv.get_excluded_paths() == [str(media_dir)]

    # 親フォルダを登録して再スキャンしても、除外したフォルダは読み込まれない
    cv.set_directories([str(media_dir.parent)])
    window.rescan_all()
    window.scan_worker.wait()
    qapp.processEvents()
    assert window.videos == []


def test_folder_dialog_lists_and_unexcludes(scanned, qapp, media_dir, tmp_path):
    from core.folder_dialog import FolderDialog

    scanned.exclude_directory_path(str(media_dir / "sub"))
    cv.set_directories([str(media_dir), str(tmp_path / "unplugged")])
    dialog = FolderDialog(scanned)

    texts = [dialog.directory_list.item(i).text() for i in range(dialog.directory_list.count())]
    assert f"{media_dir}  (2 件)" in texts
    assert any("見つかりません" in t for t in texts)
    assert dialog.excluded_list.count() == 1
    assert not dialog.unexclude_button.isEnabled()

    dialog.excluded_list.item(0).setSelected(True)
    dialog._unexclude_selected()
    assert dialog.excluded_list.count() == 0

    scanned.rescan_all()
    scanned.scan_worker.wait()
    qapp.processEvents()
    assert len(scanned.videos) == 3  # 除外を解除すると再スキャンで読み込まれる


def test_folder_dialog_unregister_keeps_files(scanned, qapp, media_dir):
    from core.folder_dialog import FolderDialog

    dialog = FolderDialog(scanned)
    dialog.directory_list.item(0).setSelected(True)
    dialog._unregister_selected()
    assert dialog.directory_list.count() == 0
    assert dialog.excluded_list.count() == 0  # 除外ではないので除外一覧には出ない
    assert cv.get_directories() == []

    scanned.reload_from_db()
    assert len(scanned.videos) == 3  # DBからは削除しない

    # 対象外になったフォルダの変更は反映されない（消えたファイルもDBに残る）
    (media_dir / "a.jpg").unlink()
    cv.set_directories([str(media_dir.parent / "other")])
    (media_dir.parent / "other").mkdir()
    scanned.rescan_all()
    scanned.scan_worker.wait()
    qapp.processEvents()
    assert len(scanned.videos) == 3


@pytest.mark.skipif(os.name != "nt", reason="ドライブ文字は Windows のみ")
def test_missing_drive_is_reported(scanned, monkeypatch):
    import string
    from core.folder_dialog import FolderDialog

    drive = next(f"{d}:" for d in reversed(string.ascii_uppercase) if not os.path.exists(f"{d}:\\"))
    cv.set_directories([os.path.join(drive + os.sep, "videos", year) for year in ("2019", "2020")])
    dialog = FolderDialog(scanned)
    assert dialog.drive_warning.isVisibleTo(dialog)
    assert f"{drive} ドライブが見つかりません" in dialog.drive_warning.text()
    assert all(f"{drive} ドライブが未接続" in dialog.directory_list.item(i).text()
               for i in range(dialog.directory_list.count()))

    # すべての登録フォルダが見つからなければ、再スキャンせずに知らせる
    warnings = []
    monkeypatch.setattr(cv.QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    scanned.rescan_all()
    assert not scanned.scanning
    assert f"{drive} ドライブが見つかりません" in warnings[0]


def test_no_drive_warning_when_folders_exist(scanned):
    from core.folder_dialog import FolderDialog
    assert not FolderDialog(scanned).drive_warning.isVisibleTo(scanned)
