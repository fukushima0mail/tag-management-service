from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QRect, QSize, QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QCompleter, QDialog, QDialogButtonBox, QLabel, QLineEdit, QListView, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout,
)

from core.formatting import MAX_RATING
from core.tags import parse_tags
from core.thumbnail_model import ID_ROLE, RATING_ROLE, ThumbnailModel

DEFAULT_CELL_WIDTH = 160  # 一覧のセル幅の目安（これより狭くなる場合は列数を減らす）
MIN_CELL_WIDTH = 100
MAX_CELL_WIDTH = 640
CELL_WIDTH_STEP = 20      # Ctrl+ホイール1段で変える幅
CELL_PADDING = 20       # セル幅とサムネイル幅の差
LABEL_HEIGHT = 45       # ファイル名（2行）の表示領域
STAR_FILLED_COLOR = QColor("#FFC107")
STAR_EMPTY_COLOR = QColor(255, 255, 255, 110)
SELECTION_BORDER_WIDTH = 4


class ThumbnailDelegate(QStyledItemDelegate):
    """サムネイルの描画: セルに合わせた拡大縮小、選択中の強調表示と、評価の★を重ねて描く"""

    _hide_icon = False

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # 標準では描画枠が元画像の大きさまでに縮められるので、一覧のサムネイルサイズに揃える
        if option.widget is not None:
            option.decorationSize = option.widget.iconSize()
        if self._hide_icon:
            # 配置（ファイル名の位置）はそのままに、標準のアイコン描画だけを止める
            option.icon = QIcon()

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        accent = opt.palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
        style = opt.widget.style()
        if selected and opt.text:
            # ファイル名の背景を薄くアクセント色にする（標準の選択表示は薄すぎるので使わない）
            text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
            painter.fillRect(text_rect, QColor(accent.red(), accent.green(), accent.blue(), 70))
        base = QStyleOptionViewItem(option)
        base.state &= ~QStyle.StateFlag.State_Selected
        self._hide_icon = True
        try:
            super().paint(painter, base, index)
        finally:
            self._hide_icon = False

        icon_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, opt, opt.widget)
        self._paint_thumbnail(painter, opt.icon, icon_rect)
        rating = index.data(RATING_ROLE)
        if rating:
            self._paint_rating(painter, opt, icon_rect, rating)
        if selected:
            self._paint_selection(painter, icon_rect, accent)

    @staticmethod
    def _paint_thumbnail(painter, icon: QIcon, rect: QRect):
        """サムネイルを縦横比を保ってセルいっぱいに描く（標準の描画は元画像より大きく拡大しないため）"""
        sizes = icon.availableSizes()
        if not sizes:
            return
        pixmap = icon.pixmap(max(sizes, key=lambda s: s.width() * s.height()))
        if pixmap.width() >= 64:  # 小さなアイコン（読み込み前の仮アイコン）は拡大しない
            pixmap = pixmap.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        x = rect.left() + (rect.width() - pixmap.width()) // 2
        y = rect.top() + (rect.height() - pixmap.height()) // 2
        painter.drawPixmap(x, y, pixmap)

    @staticmethod
    def _paint_rating(painter, opt, icon_rect: QRect, rating: int):
        """サムネイルの下端に評価の★を重ねて描画する"""
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

    @staticmethod
    def _paint_selection(painter, icon_rect: QRect, accent: QColor):
        """選択中のサムネイルに太枠と右上のチェックマークを描画する"""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(accent, SELECTION_BORDER_WIDTH)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 枠はサムネイルの内側に描く（一覧の端で切れないように）
        half = SELECTION_BORDER_WIDTH // 2
        painter.drawRect(icon_rect.adjusted(half, half, -half, -half))

        size = max(16, icon_rect.width() // 7)
        badge = QRect(icon_rect.right() - size - 3, icon_rect.top() + 3, size, size)
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.setBrush(accent)
        painter.drawEllipse(badge)
        font = painter.font()
        font.setPixelSize(int(size * 0.7))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "✓")
        painter.restore()


class ThumbnailGrid(QListView):
    """幅に合わせてセルサイズを調整するサムネイル一覧（セル幅の目安は cell_width で変えられる）"""

    zoom_requested = pyqtSignal(int)  # Ctrl+ホイールの回転量（+1 で拡大、-1 で縮小）
    selection_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumbnail_model = ThumbnailModel(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon), self)
        self.setModel(self.thumbnail_model)
        self.selectionModel().selectionChanged.connect(self.selection_changed)
        self.show_labels = True
        self.cell_width = DEFAULT_CELL_WIDTH
        self._fit_cells()

    # ------------------------------------------------------------ 一覧・選択
    def set_videos(self, videos: list[dict]):
        """一覧を入れ替える（選択中のファイルは、入れ替え後も残っていれば選択したままにする）"""
        selected = self.selected_ids()
        self.selectionModel().blockSignals(True)
        self.thumbnail_model.set_videos(videos)
        self.select_ids(selected)
        self.selectionModel().blockSignals(False)

    def count(self) -> int:
        return self.thumbnail_model.rowCount()

    def ids(self) -> list[int]:
        """表示順の video_id"""
        return self.thumbnail_model.ids()

    def selected_ids(self) -> list[int]:
        """選択中の video_id（表示順）"""
        rows = sorted(index.row() for index in self.selectionModel().selectedIndexes())
        return [self.thumbnail_model.video_at(row)["id"] for row in rows]

    def select_ids(self, ids, clear: bool = True):
        rows = [r for r in map(self.thumbnail_model.row_of, ids) if r is not None]
        self.select_rows(rows, clear)

    def select_rows(self, rows, clear: bool = True):
        selection = QItemSelection()
        for row in rows:
            index = self.thumbnail_model.index(row)
            selection.select(index, index)
        flags = QItemSelectionModel.SelectionFlag.Select
        if clear:
            flags |= QItemSelectionModel.SelectionFlag.Clear
        self.selectionModel().select(selection, flags)

    def id_at(self, pos) -> int | None:
        index = self.indexAt(pos)
        return index.data(ID_ROLE) if index.isValid() else None

    def set_cell_width(self, width: int):
        self.cell_width = max(MIN_CELL_WIDTH, min(MAX_CELL_WIDTH, width))
        self._fit_cells()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() // 120
            if steps:
                self.zoom_requested.emit(steps)
            event.accept()
            return
        super().wheelEvent(event)

    def set_show_labels(self, show: bool):
        """ファイル名を非表示にした場合は、その分セルの高さを詰める"""
        self.show_labels = show
        self.thumbnail_model.set_show_labels(show)
        self._fit_cells()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_cells()

    def _fit_cells(self):
        # スクロールバーの表示有無で列数が揺れないよう、常にその幅を差し引いて計算する
        scrollbar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        width = max(self.contentsRect().width() - scrollbar, MIN_CELL_WIDTH)
        columns = max(1, width // self.cell_width)
        cell_width = width // columns
        icon = QSize(cell_width - CELL_PADDING, (cell_width - CELL_PADDING) * 9 // 16)
        grid = QSize(cell_width, icon.height() + (LABEL_HEIGHT if self.show_labels else CELL_PADDING))
        if icon != self.iconSize() or grid != self.gridSize():
            self.setIconSize(icon)
            self.setGridSize(grid)


def make_tag_completer(model: QStringListModel, parent=None) -> QCompleter:
    """既存タグを部分一致・大文字小文字無視で補完する"""
    completer = QCompleter(model, parent)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    return completer


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

