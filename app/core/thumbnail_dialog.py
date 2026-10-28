"""動画のサムネイルにする場面を選ぶダイアログ"""
import os
import threading
from collections import deque

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QToolButton, QVBoxLayout,
)

from core.media import default_thumbnail_position, generate_video_thumbnail

CANDIDATE_COUNT = 10        # 動画全体から等間隔に並べる候補の数
SLIDER_STEPS_PER_SECOND = 10  # スライダーの刻み（0.1秒）
PREVIEW_DELAY_MS = 150      # スライダーを動かしてからプレビューを作るまでの待ち時間
PREVIEW_SIZE = QSize(640, 360)
CANDIDATE_ICON_SIZE = QSize(112, 63)
STEP_BUTTONS = (("◀◀ 10秒", -10.0), ("◀ 1秒", -1.0), ("◀ 0.1秒", -0.1),
                ("0.1秒 ▶", 0.1), ("1秒 ▶", 1.0), ("10秒 ▶▶", 10.0))


def format_position(seconds: float) -> str:
    """位置の表示（例: 1:23.4、1:02:03.4）"""
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}:{minutes:02}:{sec:04.1f}" if hours else f"{minutes}:{sec:04.1f}"


class FrameWorker(QThread):
    """ffmpeg で指定位置のフレームを取り出す（プレビューは最新の要求だけを、候補より先に処理する）"""

    frame_ready = pyqtSignal(object, float, object)  # キー（"preview" か候補の番号）, 位置, JPEG（失敗時は None）

    def __init__(self, file_path: str, duration: float, width: int, parent=None):
        super().__init__(parent)
        self.file_path, self.duration, self.width = file_path, duration, width
        self._cond = threading.Condition()
        self._jobs: deque[tuple[int, float]] = deque()
        self._preview: float | None = None

    def request_preview(self, position: float):
        with self._cond:
            self._preview = position
            self._cond.notify()

    def request(self, key: int, position: float):
        with self._cond:
            self._jobs.append((key, position))
            self._cond.notify()

    def stop(self):
        self.requestInterruption()
        with self._cond:
            self._cond.notify()
        self.wait()

    def run(self):
        while True:
            with self._cond:
                while self._preview is None and not self._jobs and not self.isInterruptionRequested():
                    self._cond.wait()
                if self.isInterruptionRequested():
                    return
                if self._preview is not None:
                    key, position, self._preview = "preview", self._preview, None
                else:
                    key, position = self._jobs.popleft()
            data = generate_video_thumbnail(self.file_path, self.duration, self.width, position)
            if self.isInterruptionRequested():
                return
            self.frame_ready.emit(key, position, data)


class ThumbnailSceneDialog(QDialog):
    """スライダーや候補の一覧から場面を選び、その位置のサムネイルを作る

    accept 後は position（秒）と thumbnail（JPEG）に結果が入る
    """

    def __init__(self, file_path: str, duration: float, width: int,
                 current_position: float | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("サムネイルの場面を選ぶ")
        self.file_path, self.duration, self.width = file_path, duration, width
        self.position = current_position if current_position is not None else default_thumbnail_position(duration)
        self.thumbnail: bytes | None = None
        self._frames: dict[float, bytes | None] = {}  # 作成済みのフレーム（位置 -> JPEG）

        name = QLabel(f"<b>{os.path.basename(file_path)}</b>")
        name.setToolTip(file_path)

        self.preview = QLabel("読み込み中...")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(PREVIEW_SIZE)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background: #111; color: #aaa;")

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, max(0, int(duration * SLIDER_STEPS_PER_SECOND) - 1))
        self.slider.setPageStep(10 * SLIDER_STEPS_PER_SECOND)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.time_label = QLabel()
        self.time_label.setMinimumWidth(130)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider_row = QHBoxLayout()
        slider_row.addWidget(self.slider, 1)
        slider_row.addWidget(self.time_label)

        step_row = QHBoxLayout()
        step_row.addStretch(1)
        for text, delta in STEP_BUTTONS:
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _, d=delta: self.set_position(self._slider_position() + d))
            step_row.addWidget(button)
        step_row.addStretch(1)

        # 動画全体から等間隔に取り出した候補（クリックでその位置へ）
        self.candidate_buttons = []
        candidate_row = QHBoxLayout()
        candidate_row.setSpacing(4)
        for i in range(CANDIDATE_COUNT):
            position = duration * (i + 0.5) / CANDIDATE_COUNT
            button = QToolButton()
            button.setIconSize(CANDIDATE_ICON_SIZE)
            button.setFixedSize(CANDIDATE_ICON_SIZE + QSize(8, 22))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setText(format_position(position))
            button.setToolTip(f"{format_position(position)} の場面にする")
            button.clicked.connect(lambda _, p=position: self.set_position(p))
            candidate_row.addWidget(button)
            self.candidate_buttons.append((button, position))

        default_button = QPushButton("既定の位置に戻す")
        default_button.setAutoDefault(False)
        default_button.setToolTip(f"スキャン時と同じ位置（{format_position(default_thumbnail_position(duration))}）にします")
        default_button.clicked.connect(lambda: self.set_position(default_thumbnail_position(duration)))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("この場面にする")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(default_button)
        bottom_row.addStretch(1)
        bottom_row.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(name)
        layout.addWidget(self.preview, 1)
        layout.addLayout(slider_row)
        layout.addLayout(step_row)
        layout.addWidget(QLabel("候補（クリックでその場面へ）"))
        layout.addLayout(candidate_row)
        layout.addLayout(bottom_row)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._request_preview)

        self.worker = FrameWorker(file_path, duration, width, self)
        self.worker.frame_ready.connect(self._on_frame_ready)
        self.worker.start()
        self.set_position(self.position)
        self._request_preview()
        for i, (_, position) in enumerate(self.candidate_buttons):
            self.worker.request(i, position)

    # ------------------------------------------------------------ 位置
    def _slider_position(self) -> float:
        return self.slider.value() / SLIDER_STEPS_PER_SECOND

    def set_position(self, seconds: float):
        value = round(seconds * SLIDER_STEPS_PER_SECOND)
        value = max(self.slider.minimum(), min(value, self.slider.maximum()))
        if value == self.slider.value():
            self._on_slider_changed(value)
        else:
            self.slider.setValue(value)

    def _on_slider_changed(self, _value: int):
        self.time_label.setText(f"{format_position(self._slider_position())} / {format_position(self.duration)}")
        self._preview_timer.start()  # ドラッグ中に何度も作らないよう、少し待ってから作る

    def _request_preview(self):
        position = self._slider_position()
        if position in self._frames:
            self._show_preview(self._frames[position])
        else:
            self.worker.request_preview(position)

    # ------------------------------------------------------------ フレーム
    def _on_frame_ready(self, key, position: float, data: bytes | None):
        self._frames[position] = data
        if key == "preview":
            if position == self._slider_position():
                self._show_preview(data)
            return
        button, _ = self.candidate_buttons[key]
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            button.setIcon(QIcon(pixmap))

    def _show_preview(self, data: bytes | None):
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self._preview_pixmap = pixmap
            self._fit_preview()
        else:
            self._preview_pixmap = None
            self.preview.setPixmap(QPixmap())
            self.preview.setText("この位置のフレームを取得できませんでした")

    def _fit_preview(self):
        pixmap = getattr(self, "_preview_pixmap", None)
        if pixmap:
            self.preview.setPixmap(pixmap.scaled(
                self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_preview()

    # ------------------------------------------------------------ 確定
    def accept(self):
        position = self._slider_position()
        data = self._frames.get(position)
        if data is None:
            data = generate_video_thumbnail(self.file_path, self.duration, self.width, position)
        if data is None:
            self.preview.setText("この位置のフレームを取得できませんでした。別の場面を選んでください")
            return
        self.position, self.thumbnail = position, data
        super().accept()

    def done(self, result: int):
        self.worker.stop()
        super().done(result)
