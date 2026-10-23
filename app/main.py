import os
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from db.init import init_table
from core.videos import VideoTagApp

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")


def set_windows_app_id():
    """タスクバーで Python ではなくこのアプリとして扱われ、アプリのアイコンが表示されるようにする"""
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("tag-management-service.VideoTagApp")


if __name__ == "__main__":
    try:
        init_table()
        set_windows_app_id()
        app = QApplication(sys.argv)
        app.setWindowIcon(QIcon(ICON_PATH))
        window = VideoTagApp()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        print(f"🚨 アプリ起動エラー: {e}")
