import sqlite3

DEFAULT_DB_PATH = 'video.db'

_db_path = DEFAULT_DB_PATH  # 現在開いているアルバムのDB


def set_db_path(path: str):
    global _db_path
    _db_path = path


def get_db_path() -> str:
    return _db_path


class DBConnection:
    def __init__(self, db_name: str | None = None):
        self.db_name = db_name or _db_path
        self.conn = None
        self.cursor = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_name)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cursor = self.conn.cursor()
        return self.cursor

    def __exit__(self, exc_type, *args, **kwargs):
        if self.conn:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        
            self.conn.close()
