import os

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPalette
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QStyle,
    QVBoxLayout,
)

from core.media import is_same_or_under, missing_drive
from db.albums import get_directories, set_directories
from db.exclusions import get_excluded_paths, remove_excluded_paths


class FolderDialog(QDialog):
    """登録フォルダ（再スキャンの対象）と、除外したフォルダ・ファイルの一覧"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app  # VideoTagApp（フォルダの除外・一覧の再読み込みに使う）
        self.setWindowTitle("フォルダ一覧")
        self.resize(640, 520)

        self.directory_list = QListWidget()
        self.directory_list.itemDoubleClicked.connect(lambda item: self._open(item.data(Qt.ItemDataRole.UserRole)))
        self.directory_list.itemSelectionChanged.connect(self._update_buttons)
        self.open_directory_button = QPushButton("エクスプローラーで開く")
        self.open_directory_button.clicked.connect(lambda: self._open(self._selected(self.directory_list)))
        self.unregister_button = QPushButton("再スキャンの対象から外す")
        self.unregister_button.setToolTip("登録済みのファイル・タグはそのまま残します（DBからは削除しません）")
        self.unregister_button.clicked.connect(self._unregister_selected)

        self.excluded_list = QListWidget()
        self.excluded_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.excluded_list.itemDoubleClicked.connect(lambda item: self._open(item.data(Qt.ItemDataRole.UserRole)))
        self.excluded_list.itemSelectionChanged.connect(self._update_buttons)
        self.open_excluded_button = QPushButton("エクスプローラーで開く")
        self.open_excluded_button.clicked.connect(lambda: self._open(self._selected(self.excluded_list)))
        self.unexclude_button = QPushButton("除外を解除")
        self.unexclude_button.setToolTip("登録フォルダ内のものは、次回の再スキャンで読み込まれます")
        self.unexclude_button.clicked.connect(self._unexclude_selected)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        self.drive_warning = QLabel()
        self.drive_warning.setWordWrap(True)
        self.drive_warning.setStyleSheet("color: #c62828;")
        self.drive_warning.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.drive_warning)
        layout.addWidget(QLabel("<b>登録フォルダ</b>（再スキャンの対象）"))
        layout.addWidget(self.directory_list, 1)
        layout.addLayout(self._button_row(self.open_directory_button, self.unregister_button))
        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>除外したフォルダ・ファイル</b>（再スキャンしても読み込まれません）"))
        layout.addWidget(self.excluded_list, 1)
        layout.addLayout(self._button_row(self.open_excluded_button, self.unexclude_button))
        layout.addWidget(buttons)

        self.refresh()

    def refresh(self):
        style = self.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        missing_color = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)

        self.directory_list.clear()
        drives = set()
        for directory in sorted(get_directories(), key=str.lower):
            count = sum(1 for v in self.app.videos if is_same_or_under(v["file_path"], directory))
            item = self._add_item(self.directory_list, directory, dir_icon, f"{directory}  ({count} 件)")
            if not os.path.isdir(directory):
                drive = missing_drive(directory)
                if drive:
                    drives.add(drive)
                    item.setText(f"{directory}  ({count} 件)  — {drive} ドライブが未接続")
                    item.setToolTip(f"{drive} ドライブが見つかりません（外付け・ネットワークドライブが未接続の可能性があります）")
                else:
                    item.setText(f"{directory}  ({count} 件)  — 見つかりません")
                    item.setToolTip("フォルダが移動・削除された可能性があります")
                item.setForeground(missing_color)
        self.drive_warning.setText(
            f"⚠ {'、'.join(sorted(drives))} ドライブが見つかりません。ドライブを接続してから再スキャンしてください。"
            "（未接続のまま再スキャンしても、登録済みのファイル・タグは削除されません）"
        )
        self.drive_warning.setVisible(bool(drives))

        self.excluded_list.clear()
        # フォルダを先に、それぞれ名前順で並べる
        excluded = sorted(get_excluded_paths(), key=lambda p: (not os.path.isdir(p), p.lower()))
        for path in excluded:
            is_dir = os.path.isdir(path) or not os.path.exists(path) and not os.path.splitext(path)[1]
            item = self._add_item(self.excluded_list, path, dir_icon if is_dir else file_icon, path)
            if not os.path.exists(path):
                drive = missing_drive(path)
                item.setText(f"{path}  — {drive} ドライブが未接続" if drive else f"{path}  — 見つかりません")
                item.setForeground(missing_color)
        self._update_buttons()

    def _unregister_selected(self):
        """登録フォルダを再スキャンの対象から外す（登録済みのファイルはDBに残す）"""
        directory = self._selected(self.directory_list)
        if not directory:
            return
        remaining = [d for d in get_directories() if d != directory]
        set_directories(remaining)
        message = f"{directory} を再スキャンの対象から外しました（登録済みのファイルはそのまま残ります）"
        if any(is_same_or_under(directory, d) for d in remaining):
            message += "。上位の登録フォルダの再スキャンでは引き続き読み込まれます"
        self.app.statusBar().showMessage(message, 10000)
        self.refresh()

    def _unexclude_selected(self):
        paths = [item.data(Qt.ItemDataRole.UserRole) for item in self.excluded_list.selectedItems()]
        if not paths:
            return
        remove_excluded_paths(paths)
        self.app.statusBar().showMessage(
            f"{len(paths)} 件の除外を解除しました（登録フォルダ内のものは次回の再スキャンで読み込まれます）", 10000,
        )
        self.refresh()

    def _update_buttons(self):
        has_directory = bool(self.directory_list.selectedItems())
        self.open_directory_button.setEnabled(has_directory)
        self.unregister_button.setEnabled(has_directory)
        self.open_excluded_button.setEnabled(len(self.excluded_list.selectedItems()) == 1)
        self.unexclude_button.setEnabled(bool(self.excluded_list.selectedItems()))

    def _open(self, path: str | None):
        if not path:
            return
        if os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self.app.reveal_file(path)

    @staticmethod
    def _add_item(widget: QListWidget, path: str, icon, text: str) -> QListWidgetItem:
        item = QListWidgetItem(icon, text)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        widget.addItem(item)
        return item

    @staticmethod
    def _selected(widget: QListWidget) -> str | None:
        items = widget.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    @staticmethod
    def _button_row(*buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        for button in buttons:
            row.addWidget(button)
        return row
