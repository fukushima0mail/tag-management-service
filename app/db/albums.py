"""アルバム（1アルバム = 1DBファイル）の管理

アルバムのDBはすべて albums/ フォルダに作成する
"""
import glob
import json
import os
from datetime import datetime

from db.connection import DBConnection

ALBUM_DIR = "albums"
DEFAULT_ALBUM_NAME = "マイアルバム"


def create_album_meta_table(db_path: str | None = None):
    """アルバム名・登録フォルダなど、アルバム自体の情報（key -> value）"""
    with DBConnection(db_path) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS album_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)


def _get_meta(key: str, db_path: str | None = None) -> str | None:
    with DBConnection(db_path) as c:
        c.execute("SELECT value FROM album_meta WHERE key = ?", (key,))
        row = c.fetchone()
    return row[0] if row else None


def _set_meta(key: str, value: str, db_path: str | None = None):
    with DBConnection(db_path) as c:
        c.execute(
            "INSERT INTO album_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def list_albums() -> list[tuple[str, str]]:
    """(DBパス, アルバム名) の一覧（作成順）"""
    albums = []
    for path in sorted(glob.glob(os.path.join(ALBUM_DIR, "*.db"))):
        create_album_meta_table(path)
        albums.append((path, _get_meta("name", path) or DEFAULT_ALBUM_NAME))
    return albums


def create_album(name: str) -> str:
    """新しいアルバムを作成し、DBパスを返す（テーブルは開くときに作成される）"""
    os.makedirs(ALBUM_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ALBUM_DIR, f"album_{stamp}.db")
    i = 2
    while os.path.exists(path):
        path = os.path.join(ALBUM_DIR, f"album_{stamp}_{i}.db")
        i += 1
    create_album_meta_table(path)
    _set_meta("name", name, path)
    return path


def rename_album(name: str):
    """現在のアルバムの名前を変更"""
    _set_meta("name", name)


def get_directories() -> list[str]:
    """現在のアルバムに登録したフォルダ（再スキャン対象）"""
    value = _get_meta("directories")
    return json.loads(value) if value else []


def set_directories(directories: list[str]):
    _set_meta("directories", json.dumps(directories, ensure_ascii=False))
