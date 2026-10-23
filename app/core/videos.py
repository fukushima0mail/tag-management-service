import os
import subprocess
import sys
from collections import Counter
from datetime import datetime

from PyQt6.QtCore import QSettings, QSize, QStringListModel, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QCompleter, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QProgressBar, QPushButton, QSplitter, QStyle,
    QVBoxLayout, QWidget,
)

from core.scanner import ScanWorker
from db.tags import add_tag, remove_tag
from db.videos import get_all_videos, get_thumbnails

ICON_SIZE = QSize(160, 90)
GRID_SIZE = QSize(180, 135)

SORT_KEYS = {
    "名前": lambda v: os.path.basename(v["file_path"]).lower(),
    "フォルダ": lambda v: v["file_path"].lower(),
    "追加日時": lambda v: v["added_at"] or "",
    "更新日時": lambda v: v["modified_at"] or 0,
    "サイズ": lambda v: v["file_size"] or 0,
    "再生時間": lambda v: v["duration"] or 0,
}
TYPE_FILTERS = {"すべて": None, "動画": "video", "画像": "image"}
TYPE_LABELS = {"video": "動画", "image": "画像"}


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{sec:02}" if hours else f"{minutes}:{sec:02}"


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def format_mtime(mtime: float | None) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "-"


class VideoTagApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("動画タグ管理アプリ")
        self.resize(1200, 750)

        self.settings = QSettings("tag-management-service", "VideoTagApp")
        self.videos: list[dict] = []
        self.video_by_id: dict[int, dict] = {}
        self.icons: dict[int, QIcon] = {}
        self.placeholder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.scan_worker: ScanWorker | None = None

        self._build_ui()
        self.reload_from_db()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # 上部ツールバー: フォルダ操作・検索・種類・並び替え
        self.add_dir_button = QPushButton("フォルダを追加...")
        self.add_dir_button.clicked.connect(self.choose_directory)
        self.rescan_button = QPushButton("再スキャン")
        self.rescan_button.clicked.connect(self.rescan_all)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ファイル名で検索")
        self.search_edit.setClearButtonEnabled(True)
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

        top = QHBoxLayout()
        top.addWidget(self.add_dir_button)
        top.addWidget(self.rescan_button)
        top.addSpacing(16)
        top.addWidget(self.search_edit, 1)
        top.addWidget(QLabel("種類:"))
        top.addWidget(self.type_combo)
        top.addWidget(QLabel("並び替え:"))
        top.addWidget(self.sort_combo)
        top.addWidget(self.order_combo)

        # 左: タグフィルター
        self.tag_filter_list = QListWidget()
        self.tag_filter_list.itemChanged.connect(self.apply_filters)
        self.tag_mode_combo = QComboBox()
        self.tag_mode_combo.addItems(["すべて含む (AND)", "いずれか含む (OR)"])
        self.tag_mode_combo.currentIndexChanged.connect(self.apply_filters)
        clear_filter_button = QPushButton("絞り込み解除")
        clear_filter_button.clicked.connect(self.clear_tag_filter)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("タグで絞り込み"))
        left_layout.addWidget(self.tag_mode_combo)
        left_layout.addWidget(self.tag_filter_list, 1)
        left_layout.addWidget(clear_filter_button)
        left = QWidget()
        left.setLayout(left_layout)

        # 中央: サムネイル一覧
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(ICON_SIZE)
        self.grid.setGridSize(GRID_SIZE)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.grid.itemSelectionChanged.connect(self.update_detail)
        self.grid.itemDoubleClicked.connect(
            lambda item: self.open_file(self.video_by_id[item.data(Qt.ItemDataRole.UserRole)]["file_path"])
        )

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
        completer = QCompleter(self.completer_model)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tag_input.setCompleter(completer)
        self.add_tag_button = QPushButton("追加")
        self.add_tag_button.clicked.connect(self.add_tag_to_selection)

        tag_input_row = QHBoxLayout()
        tag_input_row.addWidget(self.tag_input, 1)
        tag_input_row.addWidget(self.add_tag_button)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.detail_label)
        right_layout.addWidget(QLabel("タグ"))
        right_layout.addWidget(self.detail_tags, 1)
        right_layout.addWidget(self.remove_tag_button)
        right_layout.addLayout(tag_input_row)
        right = QWidget()
        right.setLayout(right_layout)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.grid)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 720, 280])

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
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().addPermanentWidget(self.count_label)

    # ---------------------------------------------------------------- データ
    def reload_from_db(self):
        """DBから一覧を再取得し、表示を更新"""
        self.videos = get_all_videos()
        self.video_by_id = {v["id"]: v for v in self.videos}

        missing = [v["id"] for v in self.videos if v["id"] not in self.icons]
        for video_id, data in get_thumbnails(missing).items():
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.icons[video_id] = QIcon(pixmap)

        self.refresh_tag_filter()
        self.completer_model.setStringList(sorted({t for v in self.videos for t in v["tags"]}, key=str.lower))
        self.apply_filters()

    def refresh_tag_filter(self):
        checked = self._checked_filter_tags()
        counts = Counter(t for v in self.videos for t in v["tags"])

        self.tag_filter_list.blockSignals(True)
        self.tag_filter_list.clear()
        for name in sorted(counts, key=str.lower):
            item = QListWidgetItem(f"{name} ({counts[name]})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked)
            self.tag_filter_list.addItem(item)
        self.tag_filter_list.blockSignals(False)

    def apply_filters(self):
        """検索・種類・タグで絞り込み、並び替えて一覧を再描画"""
        keyword = self.search_edit.text().strip().lower()
        media_type = TYPE_FILTERS[self.type_combo.currentText()]
        tags = self._checked_filter_tags()
        match_all = self.tag_mode_combo.currentIndex() == 0

        def matches(video):
            if media_type and video["media_type"] != media_type:
                return False
            if keyword and keyword not in os.path.basename(video["file_path"]).lower():
                return False
            if tags:
                video_tags = set(video["tags"])
                return tags <= video_tags if match_all else bool(tags & video_tags)
            return True

        result = sorted(
            filter(matches, self.videos),
            key=SORT_KEYS[self.sort_combo.currentText()],
            reverse=self.order_combo.currentIndex() == 1,
        )

        selected = set(self._selected_ids())
        self.grid.blockSignals(True)
        self.grid.clear()
        for video in result:
            item = QListWidgetItem(
                self.icons.get(video["id"], self.placeholder_icon),
                os.path.basename(video["file_path"]),
            )
            item.setData(Qt.ItemDataRole.UserRole, video["id"])
            item.setToolTip(
                f"{video['file_path']}\n"
                f"再生時間: {format_duration(video['duration'])}\n"
                f"タグ: {', '.join(video['tags']) or '-'}"
            )
            self.grid.addItem(item)
            if video["id"] in selected:
                item.setSelected(True)
        self.grid.blockSignals(False)

        self.count_label.setText(f"{len(result)} / {len(self.videos)} 件")
        self.update_detail()

    def update_detail(self):
        videos = [self.video_by_id[i] for i in self._selected_ids()]
        for widget in (self.tag_input, self.add_tag_button, self.remove_tag_button):
            widget.setEnabled(bool(videos))

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
                f"サイズ: {format_size(v['file_size'])}<br>"
                f"更新日時: {format_mtime(v['modified_at'])}<br>"
                f"追加日時: {v['added_at'] or '-'}<br>"
                f"<small>{v['file_path']}</small>"
            )
        else:
            self.detail_label.setText(f"<b>{len(videos)} 件選択中</b><br>追加・削除したタグは選択中のすべてに反映されます")

        counts = Counter(t for v in videos for t in v["tags"])
        for name in sorted(counts, key=str.lower):
            text = name if len(videos) == 1 else f"{name} ({counts[name]}/{len(videos)})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.detail_tags.addItem(item)

    # ---------------------------------------------------------------- 操作
    def add_tag_to_selection(self):
        name = self.tag_input.text().strip()
        ids = self._selected_ids()
        if not name or not ids:
            return
        add_tag(ids, name)
        self.tag_input.clear()
        self.reload_from_db()

    def remove_tags_from_selection(self):
        ids = self._selected_ids()
        names = [item.data(Qt.ItemDataRole.UserRole) for item in self.detail_tags.selectedItems()]
        if not ids or not names:
            return
        for name in names:
            remove_tag(ids, name)
        self.reload_from_db()

    def clear_tag_filter(self):
        self.tag_filter_list.blockSignals(True)
        for i in range(self.tag_filter_list.count()):
            self.tag_filter_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.tag_filter_list.blockSignals(False)
        self.apply_filters()

    def choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "動画・画像のフォルダを選択")
        if not directory:
            return
        directory = os.path.normpath(directory)
        directories = self._directories()
        if directory not in directories:
            directories.append(directory)
            self.settings.setValue("directories", directories)
        self.start_scan([directory])

    def rescan_all(self):
        directories = self._directories()
        if directories:
            self.start_scan(directories)
        else:
            self.choose_directory()

    def start_scan(self, directories: list[str]):
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self.add_dir_button.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.statusBar().showMessage("ファイルを検索中...")

        self.scan_worker = ScanWorker(directories, self)
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.scan_finished.connect(self.on_scan_finished)
        self.scan_worker.start()

    def on_scan_progress(self, done: int, total: int, path: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)
        self.statusBar().showMessage(f"スキャン中 ({done}/{total}): {os.path.basename(path)}")

    def on_scan_finished(self, added: int, updated: int, removed: int):
        self.add_dir_button.setEnabled(True)
        self.rescan_button.setEnabled(True)
        self.progress_bar.hide()
        self.statusBar().showMessage(f"スキャン完了: 追加 {added} / 更新 {updated} / 削除 {removed}", 10000)
        self.icons.clear()  # 更新されたファイルのサムネイルを読み直す
        self.reload_from_db()

    def open_file(self, file_path: str):
        """ダブルクリックでファイルを開く"""
        if os.name == "nt":  # Windows
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", file_path])  # macOS
        else:
            subprocess.run(["xdg-open", file_path])  # Linux

    def closeEvent(self, event):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.requestInterruption()
            self.scan_worker.wait()
        super().closeEvent(event)

    # ---------------------------------------------------------------- 補助
    def _selected_ids(self) -> list[int]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.grid.selectedItems()]

    def _checked_filter_tags(self) -> set[str]:
        return {
            self.tag_filter_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.tag_filter_list.count())
            if self.tag_filter_list.item(i).checkState() == Qt.CheckState.Checked
        }

    def _directories(self) -> list[str]:
        return self.settings.value("directories", [], type=list)
