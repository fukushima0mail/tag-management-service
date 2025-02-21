import io
import os
from PIL import Image

from PyQt6.QtWidgets import QMainWindow, QListWidget, QLabel, QVBoxLayout, QWidget, QGridLayout, QPushButton, QFileDialog
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtCore import Qt

from db.videos import get_all_videos

class VideoTagApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("動画タグ管理アプリ")
        self.setGeometry(100, 100, 800, 600)

        # メインウィジェット
        self.central_widget = QWidget()

        # レイアウト設定
        self.layout = QVBoxLayout()
        self.grid_layout = QGridLayout()  # Gridビュー用
        self.layout.addLayout(self.grid_layout)

        # 動画リストの読み込み
        self.load_videos()

        self.central_widget.setLayout(self.layout)
        self.setCentralWidget(self.central_widget)

    def load_videos(self):
        """データベースから動画リストを取得し、サムネイルを表示"""
        videos = get_all_videos()
        row, col = 0, 0

        for video_id, file_path, duration, thumbnail, added_at in videos:
            print(f"Processing: {file_path}, Thumbnail: {thumbnail is not None}")
            img_label = QLabel()
            if thumbnail:
                qt_image = self.pil_to_pixmap(thumbnail)
                img_label.setPixmap(qt_image.scaled(120, 90, Qt.AspectRatioMode.KeepAspectRatio))
            else:
                img_label.setText("No Image")

            img_label.setToolTip(f"{file_path}\nDuration: {duration:.2f}s")

            img_label.mouseDoubleClickEvent = self.open_video_event(file_path)

            self.grid_layout.addWidget(img_label, row, col)
            col += 1
            if col > 3:  # 4列ごとに改行
                col = 0
                row += 1

    def open_video_event(self, file_path):
        return lambda event: self.open_video(file_path)

    def pil_to_pixmap(self, image: Image.Image) -> QPixmap:
        """PILイメージをQPixmapに変換"""
        byte_arr = io.BytesIO()
        image.save(byte_arr, format="PNG")
        byte_data = byte_arr.getvalue()

        qimage = QImage()
        if not qimage.loadFromData(byte_data, "PNG"):
            return QPixmap()

        return QPixmap.fromImage(qimage)

    def open_video(self, file_path: str):
        """ダブルクリックで動画を開く"""
        if os.name == "nt":  # Windows
            os.startfile(file_path)
        elif os.name == "posix":
            subprocess.run(["xdg-open", file_path])  # Linux
        else:
            subprocess.run(["open", file_path])  # macOS
