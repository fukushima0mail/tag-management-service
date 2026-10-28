import os
import sqlite3
import subprocess
import sys
from collections import Counter
from urllib.parse import quote_plus

from PyQt6.QtCore import QFile, QSettings, QStringListModel, Qt, QUrl
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QProgressDialog, QPushButton, QSlider, QSplitter,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.duplicate_dialog import DuplicateDialog
from core import media
from core.duplicates import DuplicateWorker
from core.folder_dialog import FolderDialog
from core.formatting import (
    MAX_RATING, format_duration, format_mtime, format_rating, format_size, rating_stars,
)
from core.media import (
    EXTENSION_TAG_GROUP, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, get_media_type, is_same_or_under, missing_drive,
)
from core.scanner import ScanWorker
from core.tags import normalize_tag, split_tag
from core.transfer import export_album, import_album
from core.thumbnail_dialog import ThumbnailSceneDialog, format_position
from core.thumbnail_model import ID_ROLE
from core.widgets import (
    CELL_WIDTH_STEP, DEFAULT_CELL_WIDTH, MAX_CELL_WIDTH, MIN_CELL_WIDTH, AddTagsDialog, ThumbnailDelegate,
    ThumbnailGrid, make_tag_completer,
)
from db.albums import (
    DEFAULT_ALBUM_NAME, create_album, get_directories, get_grid_cell_width, get_thumbnail_width, list_albums,
    rename_album, set_directories, set_grid_cell_width, set_thumbnail_width,
)
from db.backup import backup_album, backup_daily, backup_dir, restore_album
from db.connection import get_db_path, set_db_path
from db.init import init_table
from db.exclusions import add_excluded_paths, get_excluded_paths, remove_excluded_paths
from db.tags import add_tag, delete_tags, remove_tags, rename_tags
from db.videos import (
    delete_videos, get_all_videos, get_thumbnail_position, rename_video, set_rating, set_thumbnail,
)

SORT_KEYS = {
    "名前": lambda v: os.path.basename(v["file_path"]).lower(),
    "フォルダ": lambda v: v["file_path"].lower(),
    "追加日時": lambda v: v["added_at"] or "",
    "更新日時": lambda v: v["modified_at"] or 0,
    "サイズ": lambda v: v["file_size"] or 0,
    "再生時間": lambda v: v["duration"] or 0,
    "評価": lambda v: v["rating"] or 0,
}
TYPE_FILTERS = {"すべて": None, "動画": "video", "画像": "image"}
TYPE_LABELS = {"video": "動画", "image": "画像"}
# サムネイルサイズの選択肢（表示名 -> 幅。高さは 16:9）
THUMBNAIL_WIDTHS = {"小 (240px)": 240, "標準 (320px)": 320, "大 (480px)": 480, "特大 (640px)": 640}
INVALID_FILENAME_CHARS = '\\/:*?"<>|'
GROUP_ROLE = Qt.ItemDataRole.UserRole + 2   # タグ絞り込みツリーのグループ見出しに持たせるグループ名
# 評価での絞り込み（表示名 -> 条件）
RATING_FILTERS = {"すべて": None, "未評価のみ": 0} | {f"★{i} 以上": i for i in range(1, MAX_RATING + 1)}
EXCLUDED_TAG_COLOR = QColor("#c62828")


class VideoTagApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("動画タグ管理アプリ")
        self.resize(1200, 750)

        self.settings = QSettings("tag-management-service", "VideoTagApp")
        self.videos: list[dict] = []
        self.video_by_id: dict[int, dict] = {}
        self.excluded_tags: set[str] = set()  # 絞り込みで除外するタグ（このタグが付いたファイルは表示しない）
        self.only_groups: dict[int, int] | None = None  # 重複だけを表示中: video_id -> グループ番号
        self.duplicate_worker: DuplicateWorker | None = None
        self.duplicate_dialog: DuplicateDialog | None = None
        self.scan_worker: ScanWorker | None = None
        self.scanning = False

        self._build_ui()
        self.setAcceptDrops(True)  # フォルダ・ファイルをドラッグ＆ドロップで追加
        albums = [path for path, _ in list_albums()]
        album = self.settings.value("album/current", "")
        if album not in albums:
            album = albums[0] if albums else create_album(DEFAULT_ALBUM_NAME)  # アルバムが1つもなければ作る
        self.open_album(album)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # 上部ツールバー: アルバム・フォルダ操作・検索・種類・並び替え
        self.album_combo = QComboBox()
        self.album_combo.setMinimumWidth(140)
        self.album_combo.activated.connect(self.on_album_selected)
        self.album_menu_button = QToolButton()
        self.album_menu_button.setText("…")
        self.album_menu_button.setToolTip("アルバムの作成・名前変更・削除")
        self.album_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        album_menu = QMenu(self)
        album_menu.addAction("新しいアルバム...", self.create_album)
        album_menu.addAction("アルバム名を変更...", self.rename_album)
        album_menu.addAction("サムネイルサイズ...", self.change_thumbnail_size)
        album_menu.addAction("重複ファイルを検出...", self.find_duplicates)
        album_menu.addSeparator()
        album_menu.addAction("タグ・評価をエクスポート...", self.export_tags)
        album_menu.addAction("タグ・評価をインポート...", self.import_tags)
        album_menu.addSeparator()
        album_menu.addAction("バックアップから復元...", self.restore_backup)
        album_menu.addAction("バックアップフォルダを開く", self.open_backup_folder)
        album_menu.addSeparator()
        self.delete_album_action = album_menu.addAction("アルバムを削除...", self.delete_album)
        self.album_menu_button.setMenu(album_menu)

        self.add_dir_button = QPushButton("フォルダを追加...")
        self.add_dir_button.clicked.connect(self.choose_directory)
        self.rescan_button = QPushButton("再スキャン")
        self.rescan_button.clicked.connect(self.rescan_all)
        self.exclude_dir_button = QPushButton("フォルダを除外...")
        self.exclude_dir_button.clicked.connect(self.exclude_directory)
        self.folders_button = QPushButton("フォルダ一覧...")
        self.folders_button.setToolTip("登録フォルダと、除外したフォルダ・ファイルの一覧")
        self.folders_button.clicked.connect(self.show_folders)
        self.add_files_button = QPushButton("ファイルを追加...")
        self.add_files_button.clicked.connect(self.choose_files)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ファイル名で検索")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(160)
        self.search_edit.textChanged.connect(self.apply_filters)

        self.type_combo = QComboBox()
        self.type_combo.addItems(TYPE_FILTERS)
        self.type_combo.currentIndexChanged.connect(self.apply_filters)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORT_KEYS)
        self.sort_combo.currentIndexChanged.connect(self.apply_filters)
        self.order_combo = QComboBox()
        self.order_combo.addItems(["昇順", "降順"])
        self.order_combo.currentIndexChanged.connect(self.apply_filters)

        # 一覧に並ぶサムネイルの大きさ（アルバムごとに保存。Ctrl+ホイールでも変更できる）
        self.cell_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.cell_size_slider.setRange(MIN_CELL_WIDTH, MAX_CELL_WIDTH)
        self.cell_size_slider.setSingleStep(CELL_WIDTH_STEP)
        self.cell_size_slider.setPageStep(CELL_WIDTH_STEP * 2)
        self.cell_size_slider.setValue(DEFAULT_CELL_WIDTH)
        self.cell_size_slider.setFixedWidth(120)
        self.cell_size_slider.setToolTip("一覧に並ぶサムネイルの大きさ（一覧上で Ctrl+ホイールでも変更できます）")
        self.cell_size_slider.valueChanged.connect(self.on_cell_size_changed)
        self.cell_size_slider.sliderReleased.connect(lambda: set_grid_cell_width(self.cell_size_slider.value()))

        # 1段目: ファイル操作・表示切り替え
        # （最小化したパネルは「絞り込み」「ファイル情報」から再表示）
        self.filter_toggle = QPushButton("絞り込み")
        self.detail_toggle = QPushButton("ファイル情報")
        self.label_toggle = QPushButton("ファイル名")
        self.label_toggle.setCheckable(True)
        self.label_toggle.setToolTip("サムネイル一覧のファイル名表示を切り替え")

        operation_row = QHBoxLayout()
        operation_row.addWidget(QLabel("アルバム:"))
        operation_row.addWidget(self.album_combo)
        operation_row.addWidget(self.album_menu_button)
        operation_row.addSpacing(12)
        operation_row.addWidget(self.add_dir_button)
        operation_row.addWidget(self.add_files_button)
        operation_row.addWidget(self.rescan_button)
        operation_row.addWidget(self.exclude_dir_button)
        operation_row.addWidget(self.folders_button)
        operation_row.addStretch(1)
        operation_row.addWidget(QLabel("表示:"))
        operation_row.addWidget(self.filter_toggle)
        operation_row.addWidget(self.detail_toggle)
        operation_row.addWidget(self.label_toggle)

        # 2段目: 検索・種類・並び替え
        self.only_groups_button = QPushButton()
        self.only_groups_button.setToolTip("クリックすると、すべてのファイルの表示に戻ります")
        self.only_groups_button.setStyleSheet("color: #c62828;")
        self.only_groups_button.clicked.connect(self.clear_only_groups)
        self.only_groups_button.hide()

        search_row = QHBoxLayout()
        search_row.addWidget(self.only_groups_button)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(QLabel("種類:"))
        search_row.addWidget(self.type_combo)
        search_row.addWidget(QLabel("並び替え:"))
        search_row.addWidget(self.sort_combo)
        search_row.addWidget(self.order_combo)
        search_row.addWidget(QLabel("表示サイズ:"))
        search_row.addWidget(self.cell_size_slider)

        top = QVBoxLayout()
        top.addLayout(operation_row)
        top.addLayout(search_row)

        # 左: タグフィルター
        self.tag_filter_list = QTreeWidget()
        self.tag_filter_list.setHeaderHidden(True)
        self.tag_filter_list.itemChanged.connect(self.on_tag_filter_changed)
        self.tag_filter_list.itemExpanded.connect(self.on_tag_group_toggled)
        self.tag_filter_list.itemCollapsed.connect(self.on_tag_group_toggled)
        self.tag_filter_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tag_filter_list.customContextMenuRequested.connect(self.show_tag_menu)
        self.tag_mode_combo = QComboBox()
        self.tag_mode_combo.addItems(["すべて含む (AND)", "いずれか含む (OR)"])
        self.tag_mode_combo.currentIndexChanged.connect(self.apply_filters)
        self.tag_filter_list.setToolTip("チェック: そのタグを含む / 右クリック →「除外」: そのタグを含まない")
        self.rating_filter_combo = QComboBox()
        self.rating_filter_combo.addItems(RATING_FILTERS)
        self.rating_filter_combo.currentIndexChanged.connect(self.apply_filters)
        self.untagged_check = QCheckBox("タグなしのみ（拡張子タグ以外）")
        self.untagged_check.toggled.connect(self.apply_filters)
        clear_filter_button = QPushButton("絞り込み解除")
        clear_filter_button.clicked.connect(self.clear_tag_filter)

        rating_filter_row = QHBoxLayout()
        rating_filter_row.addWidget(QLabel("評価:"))
        rating_filter_row.addWidget(self.rating_filter_combo, 1)

        left_layout = QVBoxLayout()
        left_layout.addLayout(self._panel_header("絞り込み", self.filter_toggle))
        left_layout.addLayout(rating_filter_row)
        left_layout.addWidget(self.untagged_check)
        left_layout.addWidget(QLabel("タグ"))
        left_layout.addWidget(self.tag_mode_combo)
        left_layout.addWidget(self.tag_filter_list, 1)
        left_layout.addWidget(clear_filter_button)
        left = QWidget()
        left.setLayout(left_layout)

        # 中央: サムネイル一覧
        self.grid = ThumbnailGrid()
        self.grid.setItemDelegate(ThumbnailDelegate(self.grid))
        self.grid.zoom_requested.connect(
            lambda steps: self.cell_size_slider.setValue(self.cell_size_slider.value() + steps * CELL_WIDTH_STEP)
        )
        self.grid.selection_changed.connect(self.update_detail)
        self.grid.doubleClicked.connect(lambda index: self.open_file(self.video_by_id[index.data(ID_ROLE)]["file_path"]))
        for key, slot in ((QKeySequence.StandardKey.Delete, self.exclude_selection),
                          ("Shift+Delete", self.delete_selection), ("F2", self.rename_selection),
                          ("Ctrl+Shift+C", self.copy_selected_paths)):
            shortcut = QShortcut(QKeySequence(key), self.grid)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self.show_grid_menu)

        # 右: 詳細・タグ編集
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.detail_tags = QListWidget()
        self.detail_tags.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.remove_tag_button = QPushButton("選択したタグを外す")
        self.remove_tag_button.clicked.connect(self.remove_tags_from_selection)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("タグを入力して Enter")
        self.tag_input.returnPressed.connect(self.add_tag_to_selection)
        self.completer_model = QStringListModel()
        self.tag_input.setCompleter(make_tag_completer(self.completer_model, self))
        self.add_tag_button = QPushButton("追加")
        self.add_tag_button.clicked.connect(self.add_tag_to_selection)

        tag_input_row = QHBoxLayout()
        tag_input_row.addWidget(self.tag_input, 1)
        tag_input_row.addWidget(self.add_tag_button)

        self.rating_combo = QComboBox()
        self.rating_combo.addItems(["未評価"] + [f"{rating_stars(i)}  {i}" for i in range(1, MAX_RATING + 1)])
        self.rating_combo.setPlaceholderText("（混在）")
        self.rating_combo.activated.connect(self.set_rating_to_selection)
        rating_row = QHBoxLayout()
        rating_row.addWidget(QLabel("評価:"))
        rating_row.addWidget(self.rating_combo, 1)

        self.exclude_button = QPushButton("選択したファイルをDBから除外 (Delete)")
        self.exclude_button.clicked.connect(self.exclude_selection)
        self.rename_button = QPushButton("名前を変更 (F2)")
        self.rename_button.clicked.connect(self.rename_selection)
        self.delete_button = QPushButton("ファイルを削除 (Shift+Delete)")
        self.delete_button.clicked.connect(self.delete_selection)
        file_ops_row = QHBoxLayout()
        file_ops_row.addWidget(self.rename_button)
        file_ops_row.addWidget(self.delete_button)

        right_layout = QVBoxLayout()
        right_layout.addLayout(self._panel_header("ファイル情報", self.detail_toggle))
        right_layout.addWidget(self.detail_label)
        right_layout.addLayout(rating_row)
        right_layout.addWidget(QLabel("タグ"))
        right_layout.addWidget(self.detail_tags, 1)
        right_layout.addWidget(self.remove_tag_button)
        right_layout.addLayout(tag_input_row)
        right_layout.addSpacing(12)
        right_layout.addWidget(self.exclude_button)
        right_layout.addLayout(file_ops_row)
        right = QWidget()
        right.setLayout(right_layout)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.grid)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 720, 280])

        self.label_toggle.setChecked(self.settings.value("grid/show_labels", True, type=bool))
        self.grid.set_show_labels(self.label_toggle.isChecked())
        self.label_toggle.toggled.connect(self.set_show_labels)

        for toggle, panel, key in ((self.filter_toggle, left, "filter"), (self.detail_toggle, right, "detail")):
            toggle.setCheckable(True)
            toggle.toggled.connect(panel.setVisible)
            toggle.toggled.connect(lambda on, key=key: self.settings.setValue(f"panels/{key}", on))
            toggle.setChecked(self.settings.value(f"panels/{key}", True, type=bool))
            panel.setVisible(toggle.isChecked())

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # ステータスバー
        self.count_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.hide()
        self.cancel_scan_button = QPushButton("中止")
        self.cancel_scan_button.setToolTip("スキャンを中止（処理済みのファイルは登録されたまま残ります）")
        self.cancel_scan_button.clicked.connect(self.cancel_scan)
        self.cancel_scan_button.hide()
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().addPermanentWidget(self.cancel_scan_button)
        self.statusBar().addPermanentWidget(self.count_label)

        # スキャン中は操作できないようにするもの（DBの登録内容やアルバムを変える操作）
        self.scan_locked_widgets = (
            self.add_files_button, self.add_dir_button, self.rescan_button, self.exclude_dir_button,
            self.folders_button, self.album_combo, self.album_menu_button,
        )

    # -------------------------------------------------------------- アルバム
    def open_album(self, path: str):
        """アルバム（DBファイル）を切り替えて一覧を読み込む"""
        set_db_path(path)
        init_table()
        self._backup_daily(path)
        # 旧バージョンでアプリ設定に保存していた登録フォルダは、最初に開いたアルバムへ移す
        if self.settings.contains("directories"):
            if not get_directories():
                set_directories(self.settings.value("directories", [], type=list))
            self.settings.remove("directories")
        self.settings.setValue("album/current", path)
        self.only_groups = None
        self.only_groups_button.hide()
        self.grid.clearSelection()
        self.grid.thumbnail_model.invalidate_thumbnails()
        self.cell_size_slider.blockSignals(True)
        self.cell_size_slider.setValue(get_grid_cell_width() or DEFAULT_CELL_WIDTH)
        self.cell_size_slider.blockSignals(False)
        self.grid.set_cell_width(self.cell_size_slider.value())
        self.refresh_album_combo()
        self.reload_from_db()

    def refresh_album_combo(self):
        self.album_combo.clear()
        current = get_db_path()
        for path, name in list_albums():
            self.album_combo.addItem(name, path)
            if path == current:
                self.album_combo.setCurrentIndex(self.album_combo.count() - 1)
                self.setWindowTitle(f"動画タグ管理アプリ - {name}")
        self.delete_album_action.setEnabled(self.album_combo.count() > 1)  # 最後の1つは削除できない

    def on_album_selected(self, index: int):
        path = self.album_combo.itemData(index)
        if path != get_db_path():
            self.open_album(path)

    def create_album(self):
        name, ok = QInputDialog.getText(
            self, "新しいアルバム", "アルバム名", text=f"アルバム {self.album_combo.count() + 1}",
        )
        name = name.strip()
        if ok and name:
            self.open_album(create_album(name))

    def rename_album(self):
        current = self.album_combo.currentText()
        name, ok = QInputDialog.getText(self, "アルバム名を変更", f"「{current}」の新しい名前", text=current)
        name = name.strip()
        if ok and name and name != current:
            rename_album(name)
            self.refresh_album_combo()

    def delete_album(self):
        """現在のアルバムのDBをごみ箱へ移し、別のアルバムを開く（登録していたファイル自体は削除しない）"""
        if self.album_combo.count() <= 1:
            return
        current = self.album_combo.currentText()
        answer = QMessageBox.question(
            self, "アルバムを削除",
            f"アルバム「{current}」を削除しますか？\n"
            f"このアルバムのタグ・評価・登録フォルダ（{len(self.videos)} 件分）が失われます。\n"
            "動画・画像ファイル自体は削除されません。\n"
            "（アルバムのDBファイルはごみ箱へ移動します）",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        path = get_db_path()
        if not QFile.moveToTrash(path)[0]:
            QMessageBox.warning(self, "アルバムを削除", f"DBファイルを削除できませんでした:\n{os.path.abspath(path)}")
            return
        remaining = [p for p, _ in list_albums() if p != path]
        self.open_album(remaining[0])
        self.statusBar().showMessage(f"アルバム「{current}」を削除しました", 10000)

    def on_cell_size_changed(self, width: int):
        self.grid.set_cell_width(width)
        # ドラッグ中は離したときにまとめて保存する（キーボード・ホイールでの変更はその都度保存）
        if not self.cell_size_slider.isSliderDown():
            set_grid_cell_width(width)

    def change_thumbnail_size(self):
        """このアルバムで作るサムネイルの大きさを変える（既存のサムネイルは再スキャンで作り直す）"""
        current = get_thumbnail_width()
        labels = list(THUMBNAIL_WIDTHS)
        index = next((i for i, w in enumerate(THUMBNAIL_WIDTHS.values()) if w == current), 1)
        label, ok = QInputDialog.getItem(
            self, "サムネイルサイズ",
            f"アルバム「{self.album_combo.currentText()}」のサムネイルの大きさ\n"
            "（大きいほど拡大表示がきれいになりますが、DBのサイズと読み込み時間が増えます）",
            labels, index, False,
        )
        if not ok or THUMBNAIL_WIDTHS[label] == current:
            return
        set_thumbnail_width(THUMBNAIL_WIDTHS[label])
        answer = QMessageBox.question(
            self, "サムネイルサイズ",
            f"サムネイルサイズを「{label}」にしました。\n"
            "登録済みのサムネイルは再スキャンで作り直されます。今すぐ再スキャンしますか？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.rescan_all()
        else:
            self.statusBar().showMessage(f"サムネイルサイズを「{label}」にしました（次回の再スキャンで作り直します）", 10000)

    def find_duplicates(self):
        """登録済みファイルから重複を探す（ファイルを読むのでバックグラウンドで行う）"""
        if self.scanning or (self.duplicate_worker and self.duplicate_worker.isRunning()):
            return
        progress = QProgressDialog("重複ファイルを確認しています...", "中止", 0, 0, self)
        progress.setWindowTitle("重複ファイルを検出")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        worker = DuplicateWorker(self.videos, self)
        worker.progress.connect(lambda done, total: (progress.setMaximum(total), progress.setValue(done)))
        worker.found.connect(lambda groups: self.on_duplicates_found(groups, progress))
        worker.finished.connect(progress.close)
        progress.canceled.connect(worker.requestInterruption)
        self.duplicate_worker = worker
        worker.start()

    def on_duplicates_found(self, groups: list[list[dict]], progress: QProgressDialog | None = None):
        if progress:
            progress.close()
        if not groups:
            QMessageBox.information(self, "重複ファイルを検出", "重複しているファイルは見つかりませんでした。")
            return
        self.duplicate_dialog = DuplicateDialog(self, groups)
        self.duplicate_dialog.show()

    def show_only_groups(self, groups: list[list[dict]]):
        """一覧に、指定したグループのファイルだけをグループごとに並べて表示する"""
        self.only_groups = {v["id"]: n for n, group in enumerate(groups) for v in group}
        self.only_groups_button.setText(f"重複のみ表示中（{len(groups)} グループ） ✕")
        self.only_groups_button.show()
        self.apply_filters()

    def clear_only_groups(self):
        self.only_groups = None
        self.only_groups_button.hide()
        self.apply_filters()

    def restore_backup(self):
        """バックアップを選んで、現在のアルバムをその時点の状態に戻す"""
        path = get_db_path()
        directory = backup_dir(path)
        if not os.path.isdir(directory):
            QMessageBox.information(self, "バックアップから復元", "このアルバムのバックアップはまだありません。")
            return
        backup, _ = QFileDialog.getOpenFileName(self, "復元するバックアップを選択", directory, "アルバムDB (*.db)")
        if not backup:
            return
        answer = QMessageBox.question(
            self, "バックアップから復元",
            f"アルバム「{self.album_combo.currentText()}」を、次のバックアップの状態に戻しますか？\n"
            f"{os.path.basename(backup)}\n\n"
            "今の状態も「復元前」としてバックアップしてから置き換えます。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restore_album(path, backup)
        except (OSError, sqlite3.Error) as e:
            QMessageBox.warning(self, "バックアップから復元", f"復元できませんでした:\n{e}")
            return
        self.open_album(path)
        self.statusBar().showMessage(f"バックアップから復元しました: {os.path.basename(backup)}", 10000)

    def open_backup_folder(self):
        directory = backup_dir(get_db_path())
        os.makedirs(directory, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(directory)))

    def export_tags(self):
        album = self.album_combo.currentText()
        file_path, _ = QFileDialog.getSaveFileName(
            self, "タグ・評価をエクスポート", f"{album}.json", "JSON (*.json)",
        )
        if not file_path:
            return
        try:
            count = export_album(file_path, album)
        except OSError as e:
            QMessageBox.warning(self, "エクスポート", f"書き出せませんでした:\n{e}")
            return
        self.statusBar().showMessage(f"{count} 件のタグ・評価を書き出しました: {file_path}", 10000)

    def import_tags(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "タグ・評価をインポート", "", "JSON (*.json)")
        if not file_path:
            return
        try:
            self._backup("インポート")
            matched, unmatched = import_album(file_path)
        except (OSError, ValueError) as e:  # json.JSONDecodeError は ValueError
            QMessageBox.warning(self, "インポート", f"読み込めませんでした:\n{e}")
            return
        self.reload_from_db()
        message = f"{matched} 件のファイルにタグ・評価を反映しました。"
        if unmatched:
            message += (f"\n\n{unmatched} 件はこのアルバムに見つかりませんでした。"
                        "\n先にフォルダを追加・スキャンしてから、もう一度インポートしてください。")
        QMessageBox.information(self, "インポート", message)

    # ---------------------------------------------------------------- データ
    def reload_from_db(self):
        """DBから一覧を再取得し、表示を更新（サムネイルは表示されたときに読み込む）"""
        self.videos = get_all_videos()
        self.video_by_id = {v["id"]: v for v in self.videos}
        self.refresh_tag_filter()
        self.completer_model.setStringList(sorted(self._all_tags(), key=str.lower))
        self.apply_filters()

    def refresh_tag_filter(self):
        """タグ絞り込みツリーを再構築（「グループ:値」形式のタグはグループの下にまとめる）"""
        checked = self._checked_filter_tags()
        counts = Counter(t for v in self.videos for t in v["tags"])
        groups: dict[str, list[str]] = {}
        ungrouped = []
        for name in counts:
            group, _ = split_tag(name)
            if group:
                groups.setdefault(group, []).append(name)
            else:
                ungrouped.append(name)

        self.excluded_tags &= set(counts)  # 無くなったタグの除外は解除する

        def add_tag_item(parent, name: str, text: str):
            item = QTreeWidgetItem(parent, [f"{text} ({counts[name]})"])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            item.setToolTip(0, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked)
            self._style_tag_item(item)

        self.tag_filter_list.blockSignals(True)
        self.tag_filter_list.clear()
        for group in sorted(groups, key=str.lower):
            names = groups[group]
            # グループの件数は、グループ内のいずれかのタグが付いたファイル数
            total = sum(1 for v in self.videos if any(t in names for t in v["tags"]))
            group_item = QTreeWidgetItem(self.tag_filter_list, [f"{group} ({total})"])
            group_item.setData(0, GROUP_ROLE, group)
            group_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)
            for name in sorted(names, key=lambda n: split_tag(n)[1].lower()):
                add_tag_item(group_item, name, split_tag(name)[1])
            group_item.setExpanded(group not in self._collapsed_groups())
        for name in sorted(ungrouped, key=str.lower):
            add_tag_item(self.tag_filter_list, name, name)
        self.tag_filter_list.blockSignals(False)

    def show_tag_menu(self, pos):
        """タグ絞り込みツリーの右クリックメニュー"""
        item = self.tag_filter_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        group = item.data(0, GROUP_ROLE)
        if group is None:
            name = item.data(0, Qt.ItemDataRole.UserRole)
            if name in self.excluded_tags:
                menu.addAction("除外をやめる", lambda: self.set_tag_excluded(name, False))
            else:
                menu.addAction("このタグを除外して絞り込む", lambda: self.set_tag_excluded(name, True))
            menu.addSeparator()
            menu.addAction("タグ名を変更...", lambda: self.rename_tag(name))
            menu.addAction("タグを削除...", lambda: self.delete_tags([name]))
        else:
            menu.addAction("グループ名を変更...", lambda: self.rename_group(group))
            menu.addAction("グループを解除", lambda: self.ungroup(group))
            menu.addSeparator()
            menu.addAction("グループのタグをすべて削除...", lambda: self.delete_tags(self._group_tags(group), group))
        menu.exec(self.tag_filter_list.viewport().mapToGlobal(pos))

    def on_tag_filter_changed(self, item: QTreeWidgetItem):
        # 除外中のタグにチェックを入れたら、除外をやめて「含む」条件にする
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if item.checkState(0) == Qt.CheckState.Checked and name in self.excluded_tags:
            self.excluded_tags.discard(name)
            self.tag_filter_list.blockSignals(True)
            self._style_tag_item(item)
            self.tag_filter_list.blockSignals(False)
        self.apply_filters()

    def set_tag_excluded(self, name: str, excluded: bool):
        self.tag_filter_list.blockSignals(True)
        for item in self._filter_tag_items():
            if item.data(0, Qt.ItemDataRole.UserRole) == name:
                if excluded:
                    self.excluded_tags.add(name)
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    self.excluded_tags.discard(name)
                self._style_tag_item(item)
        self.tag_filter_list.blockSignals(False)
        self.apply_filters()

    def _style_tag_item(self, item: QTreeWidgetItem):
        """除外中のタグは赤字・取り消し線で表示する"""
        excluded = item.data(0, Qt.ItemDataRole.UserRole) in self.excluded_tags
        font = item.font(0)
        font.setStrikeOut(excluded)
        item.setFont(0, font)
        item.setForeground(0, QBrush(EXCLUDED_TAG_COLOR) if excluded else QBrush())

    def delete_tags(self, names: list[str], group: str | None = None):
        """タグをすべてのファイルから外して削除する（ファイル自体は削除しない）"""
        if not names:
            return
        files = sum(1 for v in self.videos if set(v["tags"]) & set(names))
        target = f"グループ「{group}」のタグ {len(names)} 個" if group else f"タグ「{names[0]}」"
        note = ""
        if group == EXTENSION_TAG_GROUP or any(split_tag(n)[0] == EXTENSION_TAG_GROUP for n in names):
            note = "\n（拡張子タグは、ファイルが変更・移動されたときの再スキャンで付け直されます）"
        answer = QMessageBox.question(
            self, "タグを削除",
            f"{target}を、{files} 件のファイルから外して削除しますか？\nファイル自体は削除されません。{note}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._backup("タグを削除")
        delete_tags(names)
        self.excluded_tags -= set(names)
        self.reload_from_db()
        self.statusBar().showMessage(f"{target}を削除しました（{files} 件のファイルから外しました）", 10000)

    def rename_tag(self, name: str):
        new_name, ok = QInputDialog.getText(
            self, "タグ名を変更",
            f"「{name}」の新しい名前\n（「グループ:値」の形にするとグループに入ります）",
            text=name,
        )
        new_name = normalize_tag(new_name)
        if ok and new_name and new_name != name:
            self._rename_tags({name: new_name})

    def rename_group(self, group: str):
        new_group, ok = QInputDialog.getText(self, "グループ名を変更", f"「{group}」の新しいグループ名", text=group)
        new_group = new_group.replace("：", ":").strip()
        if not ok or not new_group or new_group == group:
            return
        if ":" in new_group:
            QMessageBox.warning(self, "グループ名を変更", "グループ名に「:」は使えません")
            return
        names = self._group_tags(group)
        if self._rename_tags({n: f"{new_group}:{split_tag(n)[1]}" for n in names}):
            collapsed = self._collapsed_groups()
            if group in collapsed:
                self._save_collapsed_groups(collapsed - {group} | {new_group})
                self.refresh_tag_filter()

    def ungroup(self, group: str):
        names = self._group_tags(group)
        answer = QMessageBox.question(
            self, "グループを解除",
            f"グループ「{group}」を解除しますか？\n（例: {names[0]} → {split_tag(names[0])[1]}）",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._rename_tags({n: split_tag(n)[1] for n in names})

    def _rename_tags(self, renames: dict[str, str]) -> bool:
        """タグ名を一括変更し、絞り込みのチェック状態も引き継ぐ（キャンセル時は False）"""
        existing = self._all_tags()
        merged = sorted(new for old, new in renames.items() if new in existing and new != old)
        if merged:
            answer = QMessageBox.question(
                self, "タグを統合",
                "次のタグは既にあるため、統合されます。よろしいですか？\n" + "\n".join(merged),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        if merged:
            self._backup("タグを統合")
        checked = {renames.get(t, t) for t in self._checked_filter_tags()}
        self.excluded_tags = {renames.get(t, t) for t in self.excluded_tags}
        rename_tags(renames)
        self.reload_from_db()
        self.tag_filter_list.blockSignals(True)
        for item in self._filter_tag_items():
            if item.data(0, Qt.ItemDataRole.UserRole) in checked:
                item.setCheckState(0, Qt.CheckState.Checked)
        self.tag_filter_list.blockSignals(False)
        self.apply_filters()
        self.statusBar().showMessage(f"{len(renames)} 個のタグ名を変更しました", 10000)
        return True

    def _group_tags(self, group: str) -> list[str]:
        """グループに属するタグ名の一覧"""
        return sorted(t for t in self._all_tags() if split_tag(t)[0] == group)

    def on_tag_group_toggled(self, item: QTreeWidgetItem):
        group = item.data(0, GROUP_ROLE)
        if group is None:
            return
        collapsed = self._collapsed_groups()
        self._save_collapsed_groups(collapsed - {group} if item.isExpanded() else collapsed | {group})

    def apply_filters(self):
        """検索・種類・評価・タグで絞り込み、並び替えて一覧を再描画"""
        keyword = self.search_edit.text().strip().lower()
        media_type = TYPE_FILTERS[self.type_combo.currentText()]
        tags = self._checked_filter_tags()
        excluded = self.excluded_tags
        match_all = self.tag_mode_combo.currentIndex() == 0
        min_rating = RATING_FILTERS[self.rating_filter_combo.currentText()]
        untagged_only = self.untagged_check.isChecked()
        only_groups = self.only_groups

        def matches(video):
            if media_type and video["media_type"] != media_type:
                return False
            if keyword and keyword not in os.path.basename(video["file_path"]).lower():
                return False
            rating = video["rating"] or 0
            if min_rating == 0 and rating != 0 or min_rating and rating < min_rating:
                return False
            video_tags = set(video["tags"])
            if untagged_only and any(split_tag(t)[0] != EXTENSION_TAG_GROUP for t in video_tags):
                return False
            if excluded & video_tags:
                return False
            if only_groups is not None and video["id"] not in only_groups:
                return False
            if tags:
                return tags <= video_tags if match_all else bool(tags & video_tags)
            return True

        sort_key = SORT_KEYS[self.sort_combo.currentText()]
        result = sorted(filter(matches, self.videos), key=sort_key, reverse=self.order_combo.currentIndex() == 1)
        if only_groups is not None:
            result.sort(key=lambda v: only_groups[v["id"]])  # 重複はグループごとに並べる（グループ内は並び替え順）

        self.grid.set_videos(result)

        self.count_label.setText(f"{len(result)} / {len(self.videos)} 件")
        # 絞り込みパネルを最小化していても、絞り込み中であることが分かるようにする（条件の数を表示）
        conditions = len(tags) + len(excluded) + (min_rating is not None) + untagged_only
        self.filter_toggle.setText(f"絞り込み ({conditions})" if conditions else "絞り込み")
        self.update_detail()

    def update_detail(self):
        videos = self._selected_videos()
        for widget in (self.tag_input, self.add_tag_button, self.remove_tag_button, self.rating_combo):
            widget.setEnabled(bool(videos))
        self.exclude_button.setEnabled(bool(videos) and not self.scanning)
        self.delete_button.setEnabled(bool(videos) and not self.scanning)
        self.rename_button.setEnabled(len(videos) == 1 and not self.scanning)

        ratings = {v["rating"] or 0 for v in videos}
        # 未選択時は「未評価」、評価が混在する場合は空欄（「（混在）」と表示）
        self.rating_combo.setCurrentIndex(ratings.pop() if len(ratings) == 1 else -1 if ratings else 0)

        self.detail_tags.clear()
        if not videos:
            self.detail_label.setText("ファイルを選択してください\n（Ctrl / Shift で複数選択）")
            return

        if len(videos) == 1:
            v = videos[0]
            self.detail_label.setText(
                f"<b>{os.path.basename(v['file_path'])}</b><br>"
                f"種類: {TYPE_LABELS.get(v['media_type'], v['media_type'])}<br>"
                f"再生時間: {format_duration(v['duration'])}<br>"
                f"評価: {format_rating(v['rating'])}<br>"
                f"サイズ: {format_size(v['file_size'])}<br>"
                f"更新日時: {format_mtime(v['modified_at'])}<br>"
                f"追加日時: {v['added_at'] or '-'}<br>"
                f"<small>{v['file_path']}</small>"
            )
        else:
            self.detail_label.setText(f"<b>{len(videos)} 件選択中</b><br>評価・タグの変更は選択中のすべてに反映されます")

        counts = Counter(t for v in videos for t in v["tags"])
        for name in sorted(counts, key=str.lower):
            text = name if len(videos) == 1 else f"{name} ({counts[name]}/{len(videos)})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.detail_tags.addItem(item)

    # ---------------------------------------------------------------- 操作
    def add_tag_to_selection(self):
        name = normalize_tag(self.tag_input.text())
        ids = self._selected_ids()
        if not name or not ids:
            return
        add_tag(ids, name)
        self.tag_input.clear()
        self.reload_from_db()

    def set_show_labels(self, show: bool):
        self.settings.setValue("grid/show_labels", show)
        self.grid.set_show_labels(show)

    def set_rating_to_selection(self, rating: int):
        ids = self._selected_ids()
        if not ids:
            return
        set_rating(ids, rating)
        self.reload_from_db()

    def remove_tags_from_selection(self):
        ids = self._selected_ids()
        names = [item.data(Qt.ItemDataRole.UserRole) for item in self.detail_tags.selectedItems()]
        if not ids or not names:
            return
        remove_tags(ids, names)
        self.reload_from_db()

    def exclude_selection(self):
        """選択中のファイルをDBから除外（ファイル自体は削除しない）"""
        videos = self._selected_videos()
        if not videos or self.scanning:
            return
        answer = QMessageBox.question(
            self, "DBから除外",
            f"選択中の {len(videos)} 件をDBから除外しますか？\n"
            "ファイル自体は削除されませんが、付けたタグは失われます。\n"
            "（再スキャンでは再登録されません。フォルダを追加し直すと再登録されます）",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._backup("DBから除外")
        add_excluded_paths([v["file_path"] for v in videos])
        delete_videos([v["id"] for v in videos])
        self.statusBar().showMessage(f"{len(videos)} 件をDBから除外しました", 10000)
        self.reload_from_db()

    def rename_selection(self):
        """選択中のファイル名を変更（実ファイルもリネームする）"""
        videos = self._selected_videos()
        if len(videos) != 1 or self.scanning:
            return
        video = videos[0]
        old_path = video["file_path"]
        directory, old_name = os.path.split(old_path)
        stem, ext = os.path.splitext(old_name)

        stem, ok = QInputDialog.getText(
            self, "名前を変更", f"新しいファイル名（拡張子 {ext} は変わりません）", text=stem,
        )
        stem = stem.strip()
        if not ok or not stem or stem + ext == old_name:
            return
        if any(ch in INVALID_FILENAME_CHARS for ch in stem):
            QMessageBox.warning(self, "名前を変更", f"ファイル名に次の文字は使えません:\n{INVALID_FILENAME_CHARS}")
            return
        new_path = os.path.join(directory, stem + ext)
        # 大文字・小文字だけの変更は同じファイルとして扱う
        if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(old_path):
            QMessageBox.warning(self, "名前を変更", f"同じ名前のファイルが既にあります:\n{new_path}")
            return

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            QMessageBox.warning(self, "名前を変更", f"名前を変更できませんでした:\n{e}")
            return
        try:
            rename_video(video["id"], new_path)
        except Exception as e:
            os.rename(new_path, old_path)  # DBと実ファイルの名前がずれないよう元に戻す
            QMessageBox.warning(self, "名前を変更", f"名前を変更できませんでした:\n{e}")
            return
        self.statusBar().showMessage(f"名前を変更しました: {old_name} → {stem + ext}", 10000)
        self.reload_from_db()

    def delete_selection(self):
        """選択中のファイルをごみ箱へ移動し、DBからも削除する"""
        videos = self._selected_videos()
        if not videos or self.scanning:
            return
        target = os.path.basename(videos[0]["file_path"]) if len(videos) == 1 else f"選択中の {len(videos)} 件"
        answer = QMessageBox.warning(
            self, "ファイルを削除",
            f"{target}を削除しますか？\n"
            "ファイルはごみ箱へ移動され、DBからも削除されます（付けたタグ・評価は失われます）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._backup("ファイルを削除")
        deleted, failed = [], []
        for video in videos:
            path = video["file_path"]
            # 既に実ファイルが無い場合はDBからの削除だけ行う
            if not os.path.exists(path) or QFile.moveToTrash(path)[0]:
                deleted.append(video["id"])
            else:
                failed.append(path)
        delete_videos(deleted)
        self.statusBar().showMessage(f"{len(deleted)} 件を削除しました", 10000)
        if failed:
            QMessageBox.warning(
                self, "ファイルを削除",
                "次のファイルは削除できませんでした（使用中の可能性があります）:\n" + "\n".join(failed),
            )
        self.reload_from_db()

    def exclude_directory(self):
        """選択したフォルダ内のファイルをすべてDBから除外（ファイル自体は削除しない）"""
        if self.scanning:
            return
        directories = get_directories()
        directory = QFileDialog.getExistingDirectory(
            self, "DBから除外するフォルダを選択", directories[0] if directories else "",
        )
        if not directory:
            return
        self.exclude_directory_path(os.path.normpath(directory))

    def exclude_directory_path(self, directory: str) -> bool:
        """フォルダ内のファイルをDBから除外し、再スキャンでも読み込まないよう記録する（キャンセル時は False）"""
        ids = [v["id"] for v in self.videos if is_same_or_under(v["file_path"], directory)]
        answer = QMessageBox.question(
            self, "フォルダを除外",
            f"{directory}\n\nこのフォルダ内の {len(ids)} 件をDBから除外しますか？\n"
            "ファイル自体は削除されませんが、付けたタグは失われます。\n"
            "（再スキャンでは再登録されません。フォルダを追加し直すと再登録されます）",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False

        self._backup("フォルダを除外")
        # 除外フォルダ自身・配下の登録フォルダはスキャン対象から外す
        set_directories([d for d in get_directories() if not is_same_or_under(d, directory)])
        # 除外一覧に表示し、別の登録フォルダ経由でも再登録されないよう記録する
        add_excluded_paths([directory])
        delete_videos(ids)
        self.statusBar().showMessage(f"{directory} 内の {len(ids)} 件をDBから除外しました", 10000)
        self.reload_from_db()
        return True

    def show_folders(self):
        if not self.scanning:
            FolderDialog(self).exec()

    def clear_tag_filter(self):
        """絞り込みパネルの条件（タグ・除外タグ・評価・タグなし）をすべて解除する"""
        self.excluded_tags.clear()
        for widget in (self.tag_filter_list, self.rating_filter_combo, self.untagged_check):
            widget.blockSignals(True)
        for item in self._filter_tag_items():
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self._style_tag_item(item)
        self.rating_filter_combo.setCurrentIndex(0)
        self.untagged_check.setChecked(False)
        for widget in (self.tag_filter_list, self.rating_filter_combo, self.untagged_check):
            widget.blockSignals(False)
        self.apply_filters()

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "動画・画像のフォルダを選択")
        if directory:
            self.add_paths([directory], [], "フォルダを追加")

    def choose_files(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS | IMAGE_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "追加する動画・画像を選択", "", f"動画・画像 ({patterns});;すべてのファイル (*)",
        )
        if files:
            self.add_paths([], files, "ファイルを追加")

    def add_paths(self, directories: list[str], files: list[str], title: str = "追加"):
        """フォルダ（登録フォルダになる）・ファイルを、追加時のタグを聞いてからスキャンする"""
        if self.scanning:
            return
        directories = list(dict.fromkeys(os.path.normpath(d) for d in directories))
        files = [os.path.normpath(f) for f in files if get_media_type(f)]
        if not directories and not files:
            self.statusBar().showMessage("追加できる動画・画像がありませんでした", 10000)
            return
        targets = directories + [os.path.basename(f) for f in files]
        target = targets[0] if len(targets) == 1 else (
            "、".join(filter(None, [f"フォルダ {len(directories)} 件" if directories else "",
                                    f"ファイル {len(files)} 件" if files else ""]))
        )
        dialog = self._ask_add_tags(title, target)
        if not dialog:
            return

        registered = get_directories()
        set_directories(registered + [d for d in directories if d not in registered])
        # フォルダを追加し直した場合は、以前除外したファイル・フォルダも再登録する
        # （個別に追加したファイルも、以前DBから除外していれば再登録する）
        file_keys = {os.path.normcase(f) for f in files}
        remove_excluded_paths([
            p for p in get_excluded_paths()
            if any(is_same_or_under(p, d) for d in directories) or os.path.normcase(p) in file_keys
        ])
        self.start_scan(directories, dialog.tags(), files, tag_new_only=dialog.new_only())

    def dragEnterEvent(self, event):
        if not self.scanning and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [os.path.normpath(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        event.acceptProposedAction()
        directories = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)]
        self.add_paths(directories, files, "ドラッグ＆ドロップで追加")

    def rescan_all(self):
        directories = get_directories()
        if not directories:
            self.choose_directory()
            return
        if not any(os.path.isdir(d) for d in directories):
            drives = sorted({d for d in map(missing_drive, directories) if d})
            reason = (f"{'、'.join(drives)} ドライブが見つかりません。\nドライブを接続してから再スキャンしてください。"
                      if drives else "登録フォルダが1つも見つかりません。\n「フォルダ一覧...」で登録フォルダを確認してください。")
            QMessageBox.warning(self, "再スキャン", f"{reason}\n（登録済みのファイル・タグはそのまま残っています）")
            return
        self.start_scan(directories)

    def start_scan(self, directories: list[str], tags: list[str] | None = None, files: list[str] | None = None,
                   tag_new_only: bool = False):
        if self.scanning:
            return
        self._set_scanning(True)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_scan_button.setEnabled(True)
        self.cancel_scan_button.show()
        self.statusBar().showMessage("ファイルを検索中...")

        self.scan_worker = ScanWorker(directories, tags, files, tag_new_only, parent=self)
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.scan_finished.connect(self.on_scan_finished)
        self.scan_worker.start()
        self.update_detail()

    def on_scan_progress(self, done: int, total: int, path: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)
        self.statusBar().showMessage(f"スキャン中 ({done}/{total}): {os.path.basename(path)}")

    def cancel_scan(self):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.requestInterruption()
            self.cancel_scan_button.setEnabled(False)
            self.statusBar().showMessage("スキャンを中止しています...")

    def on_scan_finished(self, added: int, updated: int, removed: int, moved: int):
        self._set_scanning(False)
        self.progress_bar.hide()
        self.cancel_scan_button.hide()
        cancelled = self.scan_worker.isInterruptionRequested()
        summary = f"追加 {added} / 更新 {updated} / 移動 {moved} / 削除 {removed}"
        self.statusBar().showMessage(f"スキャンを中止しました: {summary}" if cancelled else f"スキャン完了: {summary}", 10000)
        self.grid.thumbnail_model.invalidate_thumbnails()  # 更新されたファイルのサムネイルを読み直す
        self.reload_from_db()

    def show_grid_menu(self, pos):
        """サムネイル一覧の右クリックメニュー"""
        video_id = self.grid.id_at(pos)
        if video_id is None:
            return
        # 未選択の項目を右クリックした場合は、その項目だけを選択し直す
        if video_id not in self._selected_ids():
            self.grid.select_ids([video_id])
        video = self.video_by_id[video_id]
        selected = len(self._selected_ids())

        menu = QMenu(self)
        menu.addAction("開く", lambda: self.open_file(video["file_path"]))
        menu.addAction("エクスプローラーで表示", lambda: self.reveal_file(video["file_path"]))
        menu.addAction("パスをコピー (Ctrl+Shift+C)", self.copy_selected_paths)
        menu.addAction("Googleで検索", lambda: self.search_google(video["file_path"]))
        menu.addSeparator()
        rename = menu.addAction("名前を変更 (F2)", self.rename_selection)
        rename.setEnabled(selected == 1 and not self.scanning)
        is_video = video["media_type"] == "video"
        regenerate = menu.addAction("サムネイルの場面を選ぶ..." if is_video else "サムネイルを作り直す",
                                    lambda: self.regenerate_thumbnail(video))
        regenerate.setEnabled(selected == 1 and not self.scanning and (not is_video or media.ffmpeg is not None))
        for text, slot in (("DBから除外 (Delete)", self.exclude_selection),
                           ("ファイルを削除 (Shift+Delete)", self.delete_selection)):
            menu.addAction(text, slot).setEnabled(not self.scanning)
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def regenerate_thumbnail(self, video: dict):
        """1件のサムネイルを作り直す（動画は場面を選ぶ。選んだ位置は再スキャンで作り直すときも使う）"""
        path = video["file_path"]
        if not os.path.exists(path):
            QMessageBox.warning(self, "サムネイルを作り直す", f"ファイルが見つかりません:\n{path}")
            return
        width = get_thumbnail_width()
        if video["media_type"] == "video":
            duration = video["duration"] or media.get_video_duration(path)
            if not duration:
                QMessageBox.warning(self, "サムネイルの場面を選ぶ", f"動画の再生時間を取得できませんでした:\n{path}")
                return
            dialog = ThumbnailSceneDialog(path, duration, width, get_thumbnail_position(video["id"]), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            thumbnail, position = dialog.thumbnail, dialog.position
            message = f"サムネイルを {format_position(position)} の場面にしました"
        else:
            thumbnail, position = media.generate_image_thumbnail(path, width), None
            if thumbnail is None:
                QMessageBox.warning(self, "サムネイルを作り直す", f"サムネイルを作れませんでした:\n{path}")
                return
            message = "サムネイルを作り直しました"
        set_thumbnail(video["id"], thumbnail, width, position)
        self.grid.thumbnail_model.invalidate_thumbnail(video["id"])
        self.statusBar().showMessage(f"{message}: {os.path.basename(path)}", 5000)

    def reveal_file(self, file_path: str):
        """ファイルの場所をエクスプローラー（Finder）で開き、そのファイルを選択状態にする"""
        if not os.path.exists(file_path):
            directory = os.path.dirname(file_path)
            if not os.path.isdir(directory):
                QMessageBox.warning(self, "エクスプローラーで表示", f"ファイルが見つかりません:\n{file_path}")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))
        elif os.name == "nt":
            subprocess.Popen(f'explorer /select,"{os.path.normpath(file_path)}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", file_path])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(file_path)))

    def copy_selected_paths(self):
        """選択中のファイルのフルパスをクリップボードへ（複数なら1行に1つ）"""
        paths = [v["file_path"] for v in self._selected_videos()]
        if not paths:
            return
        QApplication.clipboard().setText("\n".join(paths))
        self.statusBar().showMessage(f"{len(paths)} 件のパスをコピーしました", 5000)

    def search_google(self, file_path: str):
        """ファイル名（拡張子なし）をブラウザでGoogle検索"""
        query = os.path.splitext(os.path.basename(file_path))[0]
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={quote_plus(query)}"))

    def open_file(self, file_path: str):
        """ダブルクリックでファイルを開く"""
        if os.name == "nt":  # Windows
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", file_path])  # macOS
        else:
            subprocess.run(["xdg-open", file_path])  # Linux

    def closeEvent(self, event):
        for worker in (self.scan_worker, self.duplicate_worker):
            if worker and worker.isRunning():
                worker.requestInterruption()
                worker.wait()
        super().closeEvent(event)

    # ---------------------------------------------------------------- 補助
    def _ask_add_tags(self, title: str, target: str) -> AddTagsDialog | None:
        """追加時に付けるタグを入力させる（キャンセル時は None）"""
        new_only = self.settings.value("add/tag_new_only", False, type=bool)
        dialog = AddTagsDialog(title, target, self.completer_model, new_only, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        self.settings.setValue("add/tag_new_only", dialog.new_only())
        return dialog

    def _panel_header(self, title: str, toggle: QPushButton) -> QHBoxLayout:
        """パネル見出し（右端の「－」で最小化）"""
        minimize_button = QToolButton()
        minimize_button.setText("－")
        minimize_button.setToolTip("最小化（上部のボタンで再表示）")
        minimize_button.setAutoRaise(True)
        minimize_button.clicked.connect(lambda: toggle.setChecked(False))
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{title}</b>"), 1)
        header.addWidget(minimize_button)
        return header

    def _backup(self, reason: str):
        """一括変更の前にアルバムのDBをバックアップする（失敗しても操作は続ける）"""
        try:
            backup_album(get_db_path(), reason)
        except (OSError, sqlite3.Error) as e:
            self.statusBar().showMessage(f"バックアップできませんでした: {e}", 10000)

    def _backup_daily(self, path: str):
        try:
            backup_daily(path)
        except (OSError, sqlite3.Error) as e:
            self.statusBar().showMessage(f"バックアップできませんでした: {e}", 10000)

    def _set_scanning(self, scanning: bool):
        self.scanning = scanning
        for widget in self.scan_locked_widgets:
            widget.setEnabled(not scanning)

    def _all_tags(self) -> set[str]:
        """現在のアルバムで使われているすべてのタグ名"""
        return {t for v in self.videos for t in v["tags"]}

    def _selected_ids(self) -> list[int]:
        return self.grid.selected_ids()

    def _selected_videos(self) -> list[dict]:
        return [self.video_by_id[i] for i in self._selected_ids()]

    def _filter_tag_items(self):
        """タグ絞り込みツリーのタグ項目（グループ見出しを除く）を列挙"""
        for i in range(self.tag_filter_list.topLevelItemCount()):
            top = self.tag_filter_list.topLevelItem(i)
            if top.data(0, GROUP_ROLE) is None:
                yield top
            else:
                yield from (top.child(j) for j in range(top.childCount()))

    def _checked_filter_tags(self) -> set[str]:
        return {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in self._filter_tag_items()
            if item.checkState(0) == Qt.CheckState.Checked
        }

    def _collapsed_groups(self) -> set[str]:
        return set(self.settings.value("filter/collapsed_groups", [], type=list))

    def _save_collapsed_groups(self, groups: set[str]):
        self.settings.setValue("filter/collapsed_groups", sorted(groups))
