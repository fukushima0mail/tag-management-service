import os
import subprocess
import sys
from collections import Counter
from datetime import datetime

from PyQt6.QtCore import QFile, QRect, QSettings, QSize, QStringListModel, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton,
    QVBoxLayout, QWidget,
)

from core.media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, get_media_type, is_same_or_under
from core.scanner import ScanWorker
from db.exclusions import add_excluded_paths, get_excluded_paths, remove_excluded_paths
from db.tags import add_tag, remove_tag
from db.videos import delete_videos, get_all_videos, get_thumbnails, rename_video, set_rating

MAX_COLUMNS = 8         # 1行に並べるサムネイルの最大数
MIN_CELL_WIDTH = 140    # これより狭くなる場合は列数を減らす
CELL_PADDING = 20       # セル幅とサムネイル幅の差
LABEL_HEIGHT = 45       # ファイル名（2行）の表示領域

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
MAX_RATING = 10
INVALID_FILENAME_CHARS = '\\/:*?"<>|'
RATING_ROLE = Qt.ItemDataRole.UserRole + 1  # サムネイル一覧の各項目に持たせる評価
STAR_FILLED_COLOR = QColor("#FFC107")
STAR_EMPTY_COLOR = QColor(255, 255, 255, 110)


def rating_stars(rating: int) -> str:
    return "★" * rating + "☆" * (MAX_RATING - rating)


def format_rating(rating: int | None) -> str:
    return f"{rating_stars(rating)} ({rating}/{MAX_RATING})" if rating else "未評価"


class RatingDelegate(QStyledItemDelegate):
    """サムネイルの下端に評価の★を重ねて描画する（未評価は何も描かない）"""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        rating = index.data(RATING_ROLE)
        if not rating:
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        icon_rect = widget.style().subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, opt, widget)

        painter.save()
        font = QFont(opt.font)
        # 10個の★がサムネイル幅に収まる大きさにする
        font.setPixelSize(max(8, icon_rect.width() // (MAX_RATING + 1)))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        strip = QRect(icon_rect.left(), icon_rect.bottom() - metrics.height(), icon_rect.width(), metrics.height() + 1)
        painter.fillRect(strip, QColor(0, 0, 0, 150))

        filled, empty = "★" * rating, "☆" * (MAX_RATING - rating)
        x = strip.left() + (strip.width() - metrics.horizontalAdvance(filled + empty)) // 2
        baseline = strip.top() + metrics.ascent()
        painter.setPen(STAR_FILLED_COLOR)
        painter.drawText(x, baseline, filled)
        painter.setPen(STAR_EMPTY_COLOR)
        painter.drawText(x + metrics.horizontalAdvance(filled), baseline, empty)
        painter.restore()


def parse_tags(text: str) -> list[str]:
    """カンマ（, 、）区切りのタグ入力を分解"""
    names = (t.strip() for t in text.replace("、", ",").split(","))
    return list(dict.fromkeys(t for t in names if t))


class ThumbnailGrid(QListWidget):
    """幅に合わせてセルサイズを調整し、1行に最大 MAX_COLUMNS 枚並べるサムネイル一覧"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.show_labels = True
        self._fit_cells()

    def set_show_labels(self, show: bool):
        """ファイル名を非表示にした場合は、その分セルの高さを詰める"""
        self.show_labels = show
        self._fit_cells()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_cells()

    def _fit_cells(self):
        # スクロールバーの表示有無で列数が揺れないよう、常にその幅を差し引いて計算する
        scrollbar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        width = max(self.contentsRect().width() - scrollbar, MIN_CELL_WIDTH)
        columns = max(1, min(MAX_COLUMNS, width // MIN_CELL_WIDTH))
        cell_width = width // columns
        icon = QSize(cell_width - CELL_PADDING, (cell_width - CELL_PADDING) * 9 // 16)
        grid = QSize(cell_width, icon.height() + (LABEL_HEIGHT if self.show_labels else CELL_PADDING))
        if icon != self.iconSize() or grid != self.gridSize():
            self.setIconSize(icon)
            self.setGridSize(grid)


class AddTagsDialog(QDialog):
    """フォルダ・ファイル追加時に、追加するすべてのファイルへ付けるタグを指定する"""

    def __init__(self, title: str, target: str, completer_model: QStringListModel,
                 new_only: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 0)

        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("例: 旅行, 2024年（空欄なら拡張子タグのみ）")
        completer = QCompleter(completer_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.tag_edit.setCompleter(completer)
        self.new_only_check = QCheckBox("登録済みのファイルにはタグを付けない（新しく登録するファイルのみ）")
        self.new_only_check.setChecked(new_only)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        target_label = QLabel(target)
        target_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(target_label)
        layout.addWidget(QLabel("追加するファイルに付けるタグ（カンマ区切り）"))
        layout.addWidget(self.tag_edit)
        layout.addWidget(self.new_only_check)
        layout.addWidget(buttons)

    def tags(self) -> list[str]:
        return parse_tags(self.tag_edit.text())

    def new_only(self) -> bool:
        return self.new_only_check.isChecked()


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
        self.scanning = False

        self._build_ui()
        self.reload_from_db()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # 上部ツールバー: フォルダ操作・検索・種類・並び替え
        self.add_dir_button = QPushButton("フォルダを追加...")
        self.add_dir_button.clicked.connect(self.choose_directory)
        self.rescan_button = QPushButton("再スキャン")
        self.rescan_button.clicked.connect(self.rescan_all)
        self.exclude_dir_button = QPushButton("フォルダを除外...")
        self.exclude_dir_button.clicked.connect(self.exclude_directory)
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

        # 1段目: ファイル操作・表示切り替え
        # （最小化したパネルは「絞り込み」「ファイル情報」から再表示）
        self.filter_toggle = QPushButton("絞り込み")
        self.detail_toggle = QPushButton("ファイル情報")
        self.label_toggle = QPushButton("ファイル名")
        self.label_toggle.setCheckable(True)
        self.label_toggle.setToolTip("サムネイル一覧のファイル名表示を切り替え")

        operation_row = QHBoxLayout()
        operation_row.addWidget(self.add_dir_button)
        operation_row.addWidget(self.add_files_button)
        operation_row.addWidget(self.rescan_button)
        operation_row.addWidget(self.exclude_dir_button)
        operation_row.addStretch(1)
        operation_row.addWidget(QLabel("表示:"))
        operation_row.addWidget(self.filter_toggle)
        operation_row.addWidget(self.detail_toggle)
        operation_row.addWidget(self.label_toggle)

        # 2段目: 検索・種類・並び替え
        search_row = QHBoxLayout()
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(QLabel("種類:"))
        search_row.addWidget(self.type_combo)
        search_row.addWidget(QLabel("並び替え:"))
        search_row.addWidget(self.sort_combo)
        search_row.addWidget(self.order_combo)

        top = QVBoxLayout()
        top.addLayout(operation_row)
        top.addLayout(search_row)

        # 左: タグフィルター
        self.tag_filter_list = QListWidget()
        self.tag_filter_list.itemChanged.connect(self.apply_filters)
        self.tag_mode_combo = QComboBox()
        self.tag_mode_combo.addItems(["すべて含む (AND)", "いずれか含む (OR)"])
        self.tag_mode_combo.currentIndexChanged.connect(self.apply_filters)
        clear_filter_button = QPushButton("絞り込み解除")
        clear_filter_button.clicked.connect(self.clear_tag_filter)

        left_layout = QVBoxLayout()
        left_layout.addLayout(self._panel_header("タグで絞り込み", self.filter_toggle))
        left_layout.addWidget(self.tag_mode_combo)
        left_layout.addWidget(self.tag_filter_list, 1)
        left_layout.addWidget(clear_filter_button)
        left = QWidget()
        left.setLayout(left_layout)

        # 中央: サムネイル一覧
        self.grid = ThumbnailGrid()
        self.grid.setItemDelegate(RatingDelegate(self.grid))
        self.grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.grid.itemSelectionChanged.connect(self.update_detail)
        self.grid.itemDoubleClicked.connect(
            lambda item: self.open_file(self.video_by_id[item.data(Qt.ItemDataRole.UserRole)]["file_path"])
        )
        delete_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self.grid)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self.exclude_selection)
        for key, slot in (("Shift+Delete", self.delete_selection), ("F2", self.rename_selection)):
            shortcut = QShortcut(QKeySequence(key), self.grid)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)

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
                os.path.basename(video["file_path"]) if self.grid.show_labels else "",
            )
            item.setData(Qt.ItemDataRole.UserRole, video["id"])
            item.setData(RATING_ROLE, video["rating"] or 0)
            item.setToolTip(
                f"{video['file_path']}\n"
                f"再生時間: {format_duration(video['duration'])}\n"
                f"評価: {format_rating(video['rating'])}\n"
                f"タグ: {', '.join(video['tags']) or '-'}"
            )
            self.grid.addItem(item)
            if video["id"] in selected:
                item.setSelected(True)
        self.grid.blockSignals(False)

        self.count_label.setText(f"{len(result)} / {len(self.videos)} 件")
        # 絞り込みパネルを最小化していても、タグで絞り込み中であることが分かるようにする
        self.filter_toggle.setText(f"絞り込み ({len(tags)})" if tags else "絞り込み")
        self.update_detail()

    def update_detail(self):
        videos = [self.video_by_id[i] for i in self._selected_ids()]
        for widget in (self.tag_input, self.add_tag_button, self.remove_tag_button, self.rating_combo):
            widget.setEnabled(bool(videos))
        self.exclude_button.setEnabled(bool(videos) and not self.scanning)
        self.delete_button.setEnabled(bool(videos) and not self.scanning)
        self.rename_button.setEnabled(len(videos) == 1 and not self.scanning)

        ratings = {v["rating"] or 0 for v in videos}
        self.rating_combo.setCurrentIndex(ratings.pop() if len(ratings) == 1 else -1)

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
        name = self.tag_input.text().strip()
        ids = self._selected_ids()
        if not name or not ids:
            return
        add_tag(ids, name)
        self.tag_input.clear()
        self.reload_from_db()

    def set_show_labels(self, show: bool):
        self.settings.setValue("grid/show_labels", show)
        self.grid.set_show_labels(show)
        self.apply_filters()

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
        for name in names:
            remove_tag(ids, name)
        self.reload_from_db()

    def exclude_selection(self):
        """選択中のファイルをDBから除外（ファイル自体は削除しない）"""
        videos = [self.video_by_id[i] for i in self._selected_ids()]
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
        add_excluded_paths([v["file_path"] for v in videos])
        delete_videos([v["id"] for v in videos])
        self.statusBar().showMessage(f"{len(videos)} 件をDBから除外しました", 10000)
        self.reload_from_db()

    def rename_selection(self):
        """選択中のファイル名を変更（実ファイルもリネームする）"""
        videos = [self.video_by_id[i] for i in self._selected_ids()]
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
        videos = [self.video_by_id[i] for i in self._selected_ids()]
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
        directories = self._directories()
        directory = QFileDialog.getExistingDirectory(
            self, "DBから除外するフォルダを選択", directories[0] if directories else "",
        )
        if not directory:
            return
        directory = os.path.normpath(directory)
        ids = [v["id"] for v in self.videos if is_same_or_under(v["file_path"], directory)]
        answer = QMessageBox.question(
            self, "フォルダを除外",
            f"{directory}\n\nこのフォルダ内の {len(ids)} 件をDBから除外しますか？\n"
            "ファイル自体は削除されませんが、付けたタグは失われます。\n"
            "（再スキャンでは再登録されません。フォルダを追加し直すと再登録されます）",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # 除外フォルダ自身・配下の登録フォルダはスキャン対象から外す
        remaining = [d for d in directories if not is_same_or_under(d, directory)]
        self.settings.setValue("directories", remaining)
        # 登録フォルダの一部だけを除外する場合は、再スキャンで再登録されないよう記録する
        if any(is_same_or_under(directory, d) for d in remaining):
            add_excluded_paths([directory])
        delete_videos(ids)
        self.statusBar().showMessage(f"{directory} 内の {len(ids)} 件をDBから除外しました", 10000)
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
        dialog = self._ask_add_tags("フォルダを追加", directory)
        if not dialog:
            return
        directories = self._directories()
        if directory not in directories:
            directories.append(directory)
            self.settings.setValue("directories", directories)
        # フォルダを追加し直した場合は、以前除外したファイル・フォルダも再登録する
        remove_excluded_paths([p for p in get_excluded_paths() if is_same_or_under(p, directory)])
        self.start_scan([directory], dialog.tags(), tag_new_only=dialog.new_only())

    def choose_files(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS | IMAGE_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "追加する動画・画像を選択", "", f"動画・画像 ({patterns});;すべてのファイル (*)",
        )
        files = [os.path.normpath(f) for f in files if get_media_type(f)]
        if not files:
            return
        target = os.path.basename(files[0]) if len(files) == 1 else f"{len(files)} 件のファイル"
        dialog = self._ask_add_tags("ファイルを追加", target)
        if not dialog:
            return
        # 以前DBから除外したファイルでも、個別に追加した場合は再登録する
        targets = {os.path.normcase(f) for f in files}
        remove_excluded_paths([p for p in get_excluded_paths() if os.path.normcase(p) in targets])
        self.start_scan([], dialog.tags(), files, tag_new_only=dialog.new_only())

    def rescan_all(self):
        directories = self._directories()
        if directories:
            self.start_scan(directories)
        else:
            self.choose_directory()

    def start_scan(self, directories: list[str], tags: list[str] | None = None, files: list[str] | None = None,
                   tag_new_only: bool = False):
        if self.scanning:
            return
        self.scanning = True
        self.add_files_button.setEnabled(False)
        self.add_dir_button.setEnabled(False)
        self.rescan_button.setEnabled(False)
        self.exclude_dir_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
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

    def on_scan_finished(self, added: int, updated: int, removed: int):
        self.scanning = False
        self.add_files_button.setEnabled(True)
        self.add_dir_button.setEnabled(True)
        self.rescan_button.setEnabled(True)
        self.exclude_dir_button.setEnabled(True)
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
