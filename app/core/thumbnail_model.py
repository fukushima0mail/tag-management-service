"""サムネイル一覧のモデル（サムネイルは表示された分だけDBから読み込み、上限を超えたら古いものから捨てる）"""
import os
from collections import OrderedDict

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap

from core.formatting import format_duration, format_rating
from db.videos import get_thumbnails

ID_ROLE = Qt.ItemDataRole.UserRole
RATING_ROLE = Qt.ItemDataRole.UserRole + 1  # サムネイル一覧の各項目に持たせる評価
CACHE_LIMIT_BYTES = 256 * 1024 * 1024       # 展開済みサムネイルを保持する上限
LOAD_BATCH_SIZE = 200                       # 1回のDB読み込みでまとめて取得する件数


class ThumbnailCache:
    """video_id -> 展開済みサムネイル（QPixmap、サムネイルなしは None）を、合計サイズの上限まで保持する"""

    def __init__(self, limit_bytes: int = CACHE_LIMIT_BYTES):
        self.limit_bytes = limit_bytes
        self._items: OrderedDict[int, QPixmap | None] = OrderedDict()
        self._bytes = 0

    def __contains__(self, video_id: int) -> bool:
        return video_id in self._items

    def __len__(self) -> int:
        return len(self._items)

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def get(self, video_id: int) -> QPixmap | None:
        self._items.move_to_end(video_id)  # 最近使ったものを後ろへ（古いものから捨てる）
        return self._items[video_id]

    def put(self, video_id: int, pixmap: QPixmap | None):
        if video_id in self._items:
            self._bytes -= _pixmap_bytes(self._items.pop(video_id))
        self._items[video_id] = pixmap
        self._bytes += _pixmap_bytes(pixmap)
        while self._bytes > self.limit_bytes and len(self._items) > 1:
            _, old = self._items.popitem(last=False)
            self._bytes -= _pixmap_bytes(old)

    def clear(self):
        self._items.clear()
        self._bytes = 0


def _pixmap_bytes(pixmap: QPixmap | None) -> int:
    return pixmap.width() * pixmap.height() * pixmap.depth() // 8 if pixmap else 0


class ThumbnailModel(QAbstractListModel):
    """絞り込み・並び替え済みの動画一覧（video dict のリスト）を表示する"""

    def __init__(self, placeholder: QIcon, parent=None):
        super().__init__(parent)
        self.placeholder = placeholder
        self.cache = ThumbnailCache()
        self.show_labels = True
        self._videos: list[dict] = []
        self._rows: dict[int, int] = {}  # video_id -> 行
        self._pending: set[int] = set()  # 読み込み待ちのサムネイル
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._load_pending)

    # ------------------------------------------------------------ 一覧
    def set_videos(self, videos: list[dict]):
        self.beginResetModel()
        self._videos = videos
        self._rows = {v["id"]: row for row, v in enumerate(videos)}
        self._pending.clear()
        self.endResetModel()

    def video_at(self, row: int) -> dict:
        return self._videos[row]

    def row_of(self, video_id: int) -> int | None:
        return self._rows.get(video_id)

    def ids(self) -> list[int]:
        return [v["id"] for v in self._videos]

    def set_show_labels(self, show: bool):
        self.show_labels = show
        if self._videos:
            self.dataChanged.emit(self.index(0), self.index(len(self._videos) - 1), [Qt.ItemDataRole.DisplayRole])

    def invalidate_thumbnails(self):
        """サムネイルを作り直した後などに、読み込み済みのサムネイルを捨てる"""
        self.cache.clear()
        if self._videos:
            self.dataChanged.emit(self.index(0), self.index(len(self._videos) - 1), [Qt.ItemDataRole.DecorationRole])

    # ------------------------------------------------------------ Qt モデル
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._videos)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        video = self._videos[index.row()]
        if role == ID_ROLE:
            return video["id"]
        if role == Qt.ItemDataRole.DisplayRole:
            return os.path.basename(video["file_path"]) if self.show_labels else ""
        if role == Qt.ItemDataRole.DecorationRole:
            return self._thumbnail(video["id"])
        if role == RATING_ROLE:
            return video["rating"] or 0
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{video['file_path']}\n"
                f"再生時間: {format_duration(video['duration'])}\n"
                f"評価: {format_rating(video['rating'])}\n"
                f"タグ: {', '.join(video['tags']) or '-'}"
            )
        return None

    # ------------------------------------------------------------ サムネイル
    def _thumbnail(self, video_id: int):
        if video_id in self.cache:
            pixmap = self.cache.get(video_id)
            return QIcon(pixmap) if pixmap else self.placeholder
        # 描画中にDBを読まないよう、まとめて後で読み込む（それまでは仮アイコン）
        self._pending.add(video_id)
        if not self._load_timer.isActive():
            self._load_timer.start(0)
        return self.placeholder

    def _load_pending(self):
        ids = list(self._pending)[:LOAD_BATCH_SIZE]
        self._pending.difference_update(ids)
        thumbnails = get_thumbnails(ids)
        rows = []
        for video_id in ids:
            pixmap = QPixmap()
            data = thumbnails.get(video_id)
            self.cache.put(video_id, pixmap if data and pixmap.loadFromData(data) else None)
            row = self._rows.get(video_id)
            if row is not None:
                rows.append(row)
        for row in rows:
            index = self.index(row)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
        if self._pending:
            self._load_timer.start(0)
