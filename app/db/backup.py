"""アルバムDBのバックアップ（albums/backup/<アルバムのファイル名>/ に世代を残す）"""
import glob
import os
import sqlite3
from datetime import datetime

from db.albums import ALBUM_DIR

BACKUP_DIR = os.path.join(ALBUM_DIR, "backup")
KEEP_BACKUPS = 10  # アルバムごとに残す世代数（古いものから削除）


def backup_dir(db_path: str) -> str:
    return os.path.join(BACKUP_DIR, os.path.splitext(os.path.basename(db_path))[0])


def list_backups(db_path: str) -> list[str]:
    """バックアップの一覧（古い順）"""
    return sorted(glob.glob(os.path.join(backup_dir(db_path), "*.db")), key=os.path.getmtime)


def backup_album(db_path: str, reason: str = "", keep: int = KEEP_BACKUPS) -> str:
    """DBをバックアップしてそのパスを返す（SQLite のバックアップ機能で、書き込み中でも整合性を保ってコピーする）"""
    directory = backup_dir(db_path)
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = f"{stamp}_{reason}.db" if reason else f"{stamp}.db"
    path = os.path.join(directory, name)
    _copy_db(db_path, path)
    for old in list_backups(db_path)[:-keep]:
        os.remove(old)
    return path


def backup_daily(db_path: str) -> str | None:
    """今日まだバックアップしていなければバックアップする（アルバムを開いたときに使う）"""
    backups = list_backups(db_path)
    today = datetime.now().date()
    if backups and datetime.fromtimestamp(os.path.getmtime(backups[-1])).date() == today:
        return None
    return backup_album(db_path, "自動")


def restore_album(db_path: str, backup_path: str) -> str:
    """バックアップの内容でアルバムを置き換える（置き換える前の状態もバックアップし、そのパスを返す）"""
    before = backup_album(db_path, "復元前")
    _copy_db(backup_path, db_path)
    return before


def _copy_db(src: str, dst: str):
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
