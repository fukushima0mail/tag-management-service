"""一覧のモデルとサムネイルの遅延読み込み"""
import io

from PIL import Image
from PyQt6.QtGui import QColor, QPixmap

from core.thumbnail_model import ThumbnailCache
from db.videos import add_videos


def _pixmap(w=100, h=50) -> QPixmap:
    p = QPixmap(w, h)
    p.fill(QColor("red"))
    return p


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (160, 90), "blue").save(buf, format="JPEG")
    return buf.getvalue()


def _register(count: int) -> list[int]:
    jpeg = _jpeg()
    return add_videos([
        (f"C:/m/{i:04}.jpg", "image", None, jpeg, 100 + i, 1.0, 320, "拡張子:jpg") for i in range(count)
    ])


def _settle(qapp, rounds: int = 20):
    for _ in range(rounds):
        qapp.processEvents()


# ---------------------------------------------------------------- キャッシュ
def test_cache_evicts_oldest_over_limit(qapp):
    one = 100 * 50 * _pixmap().depth() // 8
    cache = ThumbnailCache(limit_bytes=one * 3)
    for i in range(5):
        cache.put(i, _pixmap())
    assert len(cache) == 3
    assert 0 not in cache and 1 not in cache
    assert cache.size_bytes == one * 3


def test_cache_keeps_recently_used(qapp):
    one = 100 * 50 * _pixmap().depth() // 8
    cache = ThumbnailCache(limit_bytes=one * 2)
    cache.put(1, _pixmap())
    cache.put(2, _pixmap())
    cache.get(1)                # 1 を使うと、次に捨てられるのは 2
    cache.put(3, _pixmap())
    assert 1 in cache and 3 in cache and 2 not in cache


def test_cache_none_costs_nothing(qapp):
    cache = ThumbnailCache(limit_bytes=1)
    cache.put(1, None)          # サムネイルなしも「読み込み済み」として覚える
    assert 1 in cache and cache.size_bytes == 0


# ---------------------------------------------------------------- 遅延読み込み
def test_thumbnails_are_not_loaded_until_shown(window, qapp):
    _register(5)
    window.reload_from_db()
    assert len(window.grid.thumbnail_model.cache) == 0   # 一覧を作っただけでは読み込まない

    window.show()
    _settle(qapp)
    assert len(window.grid.thumbnail_model.cache) == 5


def test_only_visible_thumbnails_are_loaded(window, qapp):
    _register(400)
    window.resize(900, 600)
    window.cell_size_slider.setValue(300)
    window.reload_from_db()
    window.show()
    _settle(qapp)
    loaded = len(window.grid.thumbnail_model.cache)
    assert 0 < loaded < 100  # 400 件のうち、画面に見えている分だけ


def test_selection_is_kept_when_filter_changes(window, qapp):
    ids = _register(5)
    window.reload_from_db()
    window.grid.select_ids(ids[:2])
    window.search_edit.setText("000")   # 0000〜0004 のすべてに一致
    assert sorted(window._selected_ids()) == sorted(ids[:2])
    window.search_edit.setText("0001")
    assert window._selected_ids() == [ids[1]]


def test_invalidate_after_scan_reloads(window, qapp):
    _register(3)
    window.reload_from_db()
    window.show()
    _settle(qapp)
    assert len(window.grid.thumbnail_model.cache) == 3
    window.grid.thumbnail_model.invalidate_thumbnails()
    assert len(window.grid.thumbnail_model.cache) == 0
    _settle(qapp)
    assert len(window.grid.thumbnail_model.cache) == 3
