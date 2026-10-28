import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from core.duplicates import keep_candidate
from core.formatting import format_size


class DuplicateDialog(QDialog):
    """重複ファイルの一覧。一覧（メイン画面）に重複だけを表示したり、1件ずつ残して選択したりできる"""

    def __init__(self, app, groups: list[list[dict]]):
        super().__init__(app)
        self.app = app  # VideoTagApp
        self.groups = groups
        self.setWindowTitle("重複ファイル")
        self.resize(760, 520)

        files = sum(len(g) for g in groups)
        wasted = sum(g[0]["file_size"] * (len(g) - 1) for g in groups)
        summary = QLabel(
            f"<b>{len(groups)} グループ・{files} 件</b>の重複が見つかりました"
            f"（1件ずつ残すと {format_size(wasted)} 減らせます）<br>"
            "<small>サイズと、ファイルの先頭・中央・末尾の内容が一致するものを重複としています。"
            "★ は評価・タグ・追加日時から選んだ「残す候補」です。</small>"
        )
        summary.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["ファイル", "評価", "タグ数", "追加日時"])
        for column, width in enumerate((470, 50, 60)):
            self.tree.setColumnWidth(column, width)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)  # 長いパスでもファイル名が見えるように
        self.tree.itemDoubleClicked.connect(self._reveal)
        for n, group in enumerate(groups, 1):
            keep = keep_candidate(group)
            head = QTreeWidgetItem(self.tree, [f"グループ {n}: {len(group)} 件 · {format_size(group[0]['file_size'])}"])
            font = QFont(head.font(0))
            font.setBold(True)
            head.setFont(0, font)
            for video in group:
                mark = "★ " if video is keep else "　 "
                item = QTreeWidgetItem(head, [
                    mark + video["file_path"], str(video["rating"] or "-"), str(len(video["tags"])),
                    video["added_at"] or "-",
                ])
                item.setData(0, Qt.ItemDataRole.UserRole, video["file_path"])
                item.setToolTip(0, video["file_path"])
            head.setExpanded(True)

        show_button = QPushButton("一覧に重複だけを表示")
        show_button.setToolTip("メイン画面の一覧を、重複ファイルだけ（グループごとに並べて）表示します")
        show_button.clicked.connect(self.show_in_list)
        select_button = QPushButton("1件ずつ残して選択")
        select_button.setToolTip("各グループの ★ 以外を一覧で選択します（そのまま Delete / Shift+Delete で除外・削除できます）")
        select_button.clicked.connect(self.select_extras)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("閉じる")
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(show_button)
        row.addWidget(select_button)
        row.addStretch(1)
        row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree, 1)
        layout.addLayout(row)

    def show_in_list(self):
        self.app.show_only_groups(self.groups)

    def select_extras(self):
        self.app.show_only_groups(self.groups)
        self.app.grid.select_ids([v["id"] for g in self.groups for v in g if v is not keep_candidate(g)])
        self.app.grid.setFocus()

    def _reveal(self, item: QTreeWidgetItem):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path and os.path.exists(path):
            self.app.reveal_file(path)
