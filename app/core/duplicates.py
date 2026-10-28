"""重複ファイルの検出（サイズが同じファイルだけ、先頭・中央・末尾の内容を比べる）"""
import hashlib

from PyQt6.QtCore import QThread, pyqtSignal

SAMPLE_SIZE = 1024 * 1024  # 比べる範囲（先頭・中央・末尾それぞれ）


def sample_hash(path: str, size: int) -> str:
    """ファイルの先頭・中央・末尾 SAMPLE_SIZE ずつのハッシュ（小さなファイルは全体）

    動画のように大きなファイルを全部読まずに比べるため。サイズも一致していることが前提
    """
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        if size <= SAMPLE_SIZE * 3:
            digest.update(f.read())
        else:
            for offset in (0, size // 2 - SAMPLE_SIZE // 2, size - SAMPLE_SIZE):
                f.seek(offset)
                digest.update(f.read(SAMPLE_SIZE))
    return digest.hexdigest()


def keep_candidate(group: list[dict]) -> dict:
    """重複グループの中で残すファイル（評価が高い → タグが多い → 先に追加した順）"""
    return min(group, key=lambda v: (-(v["rating"] or 0), -len(v["tags"]), v["added_at"] or "", v["id"]))


class DuplicateWorker(QThread):
    """登録済みファイルから重複を探す（ファイルを読むのでバックグラウンドで行う）"""

    progress = pyqtSignal(int, int)        # 確認済み件数, 確認する件数
    found = pyqtSignal(list)               # 重複グループ（video dict のリストのリスト）

    def __init__(self, videos: list[dict], parent=None):
        super().__init__(parent)
        self.videos = videos

    def run(self):
        by_size: dict[int, list[dict]] = {}
        for video in self.videos:
            if video["file_size"]:
                by_size.setdefault(video["file_size"], []).append(video)
        candidates = [v for group in by_size.values() if len(group) > 1 for v in group]

        by_hash: dict[tuple, list[dict]] = {}
        for i, video in enumerate(candidates, 1):
            if self.isInterruptionRequested():
                return
            self.progress.emit(i, len(candidates))
            try:
                key = (video["file_size"], sample_hash(video["file_path"], video["file_size"]))
            except OSError:  # 見つからない・読めないファイルは対象外
                continue
            by_hash.setdefault(key, []).append(video)

        groups = [sorted(g, key=lambda v: v["file_path"].lower()) for g in by_hash.values() if len(g) > 1]
        groups.sort(key=lambda g: -g[0]["file_size"])  # 大きいファイルから
        self.found.emit(groups)
