import sys

from PyQt6.QtWidgets import QApplication

from db.init import init_table
from core.videos import VideoTagApp

if __name__ == "__main__":
    try:
        init_table()
        app = QApplication(sys.argv)
        window = VideoTagApp()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        print(f"🚨 アプリ起動エラー: {e}")
