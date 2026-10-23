import os

from PyQt6.QtCore import QThread, pyqtSignal

from core.media import extension_tag, iter_media_files, load_media_info
from db.tags import add_tag
from db.videos import add_video, delete_videos, get_video_index


def _is_under(path: str, directory: str) -> bool:
    return os.path.normcase(path).startswith(os.path.normcase(os.path.join(directory, "")))


class ScanWorker(QThread):
    """ディレクトリをバックグラウンドでスキャンし、DBへ登録する"""

    progress = pyqtSignal(int, int, str)       # 処理済み件数, 総件数, ファイルパス
    scan_finished = pyqtSignal(int, int, int)  # 追加, 更新, 削除

    def __init__(self, directories: list[str], parent=None):
        super().__init__(parent)
        self.directories = [os.path.normpath(d) for d in directories]

    def run(self):
        files = []
        for directory in self.directories:
            files.extend(iter_media_files(directory))

        index = get_video_index()
        added = updated = 0
        for i, path in enumerate(files, 1):
            if self.isInterruptionRequested():
                self.scan_finished.emit(added, updated, 0)
                return
            self.progress.emit(i, len(files), path)

            try:
                stat = os.stat(path)
            except OSError:
                continue

            # サイズ・更新日時が変わっていないファイルは再処理しない
            existing = index.get(path)
            if existing and existing[1] == stat.st_size and existing[2] == stat.st_mtime:
                continue

            media_type, duration, thumbnail = load_media_info(path)
            video_id = add_video(path, media_type, duration, thumbnail, stat.st_size, stat.st_mtime)
            add_tag([video_id], extension_tag(path))
            if existing:
                updated += 1
            else:
                added += 1

        # スキャン対象フォルダから消えたファイルをDBから削除
        # （フォルダ自体が見つからない場合は、外付けドライブ未接続などを考慮して削除しない）
        found = set(files)
        available = [d for d in self.directories if os.path.isdir(d)]
        stale = [
            video_id for path, (video_id, _, _) in index.items()
            if path not in found and any(_is_under(path, d) for d in available)
        ]
        delete_videos(stale)
        self.scan_finished.emit(added, updated, len(stale))
