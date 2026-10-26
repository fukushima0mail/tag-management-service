import os

from PyQt6.QtCore import QThread, pyqtSignal

from core.media import extension_tag, is_same_or_under, iter_media_files, load_media_info
from db.exclusions import get_excluded_paths
from db.tags import add_tag
from db.videos import add_video, delete_videos, get_video_index, rename_video


def _is_excluded(path: str, excluded: set[str]) -> bool:
    """path 自身、またはその上位フォルダのいずれかが除外対象か"""
    path = os.path.normcase(path)
    while True:
        if path in excluded:
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def _missing_files(index: dict[str, tuple[int, int | None, float | None]]) -> dict[tuple, list[tuple[str, int]]]:
    """実ファイルが見つからない登録済みファイルを (サイズ, 更新日時) ごとにまとめる

    移動・リネームしてもサイズと更新日時は変わらないので、これが一致する新しいファイルを同じものとみなす
    """
    missing = {}
    for path, (video_id, size, mtime) in index.items():
        if size is not None and mtime is not None and not os.path.exists(path):
            missing.setdefault((size, mtime), []).append((path, video_id))
    return missing


class ScanWorker(QThread):
    """ディレクトリをバックグラウンドでスキャンし、DBへ登録する"""

    progress = pyqtSignal(int, int, str)       # 処理済み件数, 総件数, ファイルパス
    scan_finished = pyqtSignal(int, int, int, int)  # 追加, 更新, 削除, 移動

    def __init__(self, directories: list[str], tags: list[str] | None = None,
                 files: list[str] | None = None, tag_new_only: bool = False, parent=None):
        super().__init__(parent)
        self.directories = [os.path.normpath(d) for d in directories]
        self.tags = tags or []  # スキャン対象のファイルに付与するタグ
        self.tag_new_only = tag_new_only  # True なら、スキャン開始時点で未登録のファイルにだけタグを付与
        self.files = [os.path.normpath(f) for f in files or []]  # 個別に追加するファイル

    def run(self):
        files = self._collect_files()
        index = get_video_index()
        added = updated = moved = 0
        missing = None  # (サイズ, 更新日時) -> 実ファイルが見つからない登録済みファイルの (パス, ID)
        scanned_ids = []
        for i, path in enumerate(files, 1):
            if self.isInterruptionRequested():
                break
            self.progress.emit(i, len(files), path)

            try:
                stat = os.stat(path)
            except OSError:
                continue

            # サイズ・更新日時が変わっていないファイルは再処理しない
            existing = index.get(path)
            if existing:
                existing_id, size, mtime = existing
                if size == stat.st_size and mtime == stat.st_mtime:
                    if not self.tag_new_only:
                        scanned_ids.append(existing_id)
                    continue
            else:
                # 移動・リネームされたファイルは、登録済みの情報（タグ・評価）を引き継ぐ
                if missing is None:
                    missing = _missing_files(index)
                candidates = missing.get((stat.st_size, stat.st_mtime))
                if candidates:
                    old_path, video_id = candidates.pop()
                    rename_video(video_id, path)
                    del index[old_path]
                    index[path] = (video_id, stat.st_size, stat.st_mtime)
                    add_tag([video_id], extension_tag(path))
                    if not self.tag_new_only:
                        scanned_ids.append(video_id)
                    moved += 1
                    continue

            media_type, duration, thumbnail = load_media_info(path)
            video_id = add_video(path, media_type, duration, thumbnail, stat.st_size, stat.st_mtime)
            add_tag([video_id], extension_tag(path))
            if not (existing and self.tag_new_only):
                scanned_ids.append(video_id)
            if existing:
                updated += 1
            else:
                added += 1

        self._add_tags(scanned_ids)
        if self.isInterruptionRequested():
            # 中止した場合は、未確認のファイルを消してしまわないよう削除は行わない
            self.scan_finished.emit(added, updated, 0, moved)
            return

        # スキャン対象フォルダから消えたファイルをDBから削除
        # （フォルダ自体が見つからない場合は、外付けドライブ未接続などを考慮して削除しない）
        # （除外フォルダ内から個別に追加したファイルは、スキャン対象外でも実在すれば残す）
        found = set(files)
        available = [d for d in self.directories if os.path.isdir(d)]
        stale = [
            video_id for path, (video_id, _, _) in index.items()
            if path not in found and any(is_same_or_under(path, d) for d in available)
            and not os.path.exists(path)
        ]
        delete_videos(stale)
        self.scan_finished.emit(added, updated, len(stale), moved)

    def _collect_files(self) -> list[str]:
        """スキャン対象のファイル一覧（中止された時点で打ち切る）"""
        excluded = get_excluded_paths()
        files = list(self.files)
        for directory in self.directories:
            # 明示的に追加したフォルダは、その上位フォルダの除外設定より優先する
            rules = {os.path.normcase(e) for e in excluded if not is_same_or_under(directory, e)}
            for path in iter_media_files(directory):
                if self.isInterruptionRequested():
                    return files
                if not _is_excluded(path, rules):
                    files.append(path)
        return files

    def _add_tags(self, video_ids: list[int]):
        if video_ids:
            for name in self.tags:
                add_tag(video_ids, name)
