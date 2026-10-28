"""一覧に並ぶサムネイルの大きさ（アルバムごと）"""
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent

import core.videos as cv
from core.widgets import CELL_WIDTH_STEP, DEFAULT_CELL_WIDTH, MAX_CELL_WIDTH, MIN_CELL_WIDTH, ThumbnailGrid
from db.albums import get_grid_cell_width


def columns(grid: ThumbnailGrid) -> int:
    grid.resize(1000, 600)
    grid._fit_cells()
    return max(1, round(grid.viewport().width() / grid.gridSize().width()))


def wheel(grid: ThumbnailGrid, delta: int, modifiers=Qt.KeyboardModifier.ControlModifier):
    event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, delta),
                        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    grid.wheelEvent(event)


def test_cell_width_changes_columns(qapp):
    grid = ThumbnailGrid()
    grid.set_cell_width(120)
    many = columns(grid)
    grid.set_cell_width(480)
    few = columns(grid)
    assert many > few >= 1
    # セルは一覧の幅いっぱいに並べ、サムネイルは 16:9
    assert grid.iconSize().height() == grid.iconSize().width() * 9 // 16


def test_cell_width_is_clamped(qapp):
    grid = ThumbnailGrid()
    grid.set_cell_width(10)
    assert grid.cell_width == MIN_CELL_WIDTH
    grid.set_cell_width(10000)
    assert grid.cell_width == MAX_CELL_WIDTH


def test_ctrl_wheel_requests_zoom(qapp):
    grid = ThumbnailGrid()
    steps = []
    grid.zoom_requested.connect(steps.append)
    wheel(grid, 120)
    wheel(grid, -240)
    wheel(grid, 120, Qt.KeyboardModifier.NoModifier)  # Ctrl なしは通常のスクロール
    assert steps == [1, -2]


def test_slider_changes_grid_and_saves_per_album(window, monkeypatch):
    assert window.cell_size_slider.value() == DEFAULT_CELL_WIDTH
    assert get_grid_cell_width() is None

    window.cell_size_slider.setValue(300)
    assert window.grid.cell_width == 300
    assert get_grid_cell_width() == 300

    monkeypatch.setattr(cv.QInputDialog, "getText", staticmethod(lambda *a, **k: ("別", True)))
    window.create_album()
    assert window.cell_size_slider.value() == DEFAULT_CELL_WIDTH  # 新しいアルバムは既定の大きさ
    assert window.grid.cell_width == DEFAULT_CELL_WIDTH

    window.on_album_selected(0)
    assert window.cell_size_slider.value() == 300
    assert window.grid.cell_width == 300


def test_ctrl_wheel_moves_slider(window):
    window.cell_size_slider.setValue(200)
    wheel(window.grid, 120)
    assert window.cell_size_slider.value() == 200 + CELL_WIDTH_STEP
    assert get_grid_cell_width() == 200 + CELL_WIDTH_STEP


def test_size_is_kept_after_restart(window):
    window.cell_size_slider.setValue(400)
    window.close()
    reopened = cv.VideoTagApp()
    assert reopened.grid.cell_width == 400
    reopened.close()


def test_small_thumbnail_is_scaled_up_to_cell(qapp):
    """元画像（320px）より大きいセルでは、サムネイルをセルいっぱいに拡大して描く"""
    from PyQt6.QtGui import QColor, QPixmap
    from core.widgets import ThumbnailDelegate

    grid = ThumbnailGrid()
    grid.setItemDelegate(ThumbnailDelegate(grid))
    grid.resize(700, 500)
    grid.set_cell_width(640)
    pixmap = QPixmap(320, 180)
    pixmap.fill(QColor("red"))
    grid.thumbnail_model.cache.put(1, pixmap)  # 読み込み済みのサムネイル
    grid.set_videos([{"id": 1, "file_path": "a.jpg", "rating": 0, "duration": None, "tags": []}])
    grid.show()
    qapp.processEvents()

    rect = grid.visualRect(grid.thumbnail_model.index(0))
    assert grid.iconSize().width() > 320
    image = grid.viewport().grab().toImage()
    # 元画像の幅（320px）を超えた位置まで赤く塗られている
    sample = image.pixelColor(rect.left() + grid.iconSize().width() - 20, rect.top() + grid.iconSize().height() // 2)
    assert (sample.red(), sample.green(), sample.blue()) == (255, 0, 0)
    grid.close()
