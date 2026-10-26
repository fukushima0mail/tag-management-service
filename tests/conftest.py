import os
import sys

# 画面を表示せずに Qt を動かす（Qt を import する前に設定する）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import pytest
from PIL import Image
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QMessageBox

from db.albums import create_album
from db.connection import set_db_path
from db.init import init_table


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def album(tmp_path, monkeypatch):
    """一時フォルダをカレントにして、空のアルバムを1つ作って開く（DBパスを返す）"""
    monkeypatch.chdir(tmp_path)
    path = create_album("テスト")
    set_db_path(path)
    init_table()
    return path


@pytest.fixture
def media_dir(tmp_path):
    """画像3枚（a.jpg, b.png, sub/c.jpg）と対象外ファイルを置いたフォルダ"""
    root = tmp_path / "media"
    (root / "sub").mkdir(parents=True)
    for name in ("a.jpg", "b.png", "sub/c.jpg"):
        Image.new("RGB", (64, 48), "red").save(root / name)
    (root / "memo.txt").write_text("not media")
    return root


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """設定を一時ファイルに保存し、確認ダイアログは常に「はい」にしたメインウィンドウ"""
    import core.videos as cv

    monkeypatch.chdir(tmp_path)
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(cv, "QSettings", lambda *a: QSettings(ini, QSettings.Format.IniFormat))
    monkeypatch.setattr(cv.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = cv.VideoTagApp()
    yield w
    w.close()
