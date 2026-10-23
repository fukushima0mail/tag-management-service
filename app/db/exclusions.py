from db.connection import DBConnection


def create_excluded_paths_table():
    """DBから除外したファイル・フォルダ（再スキャンで再登録しない）"""
    with DBConnection() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS excluded_paths (
                path TEXT PRIMARY KEY
            )
        """)


def add_excluded_paths(paths: list[str]):
    with DBConnection() as c:
        c.executemany("INSERT OR IGNORE INTO excluded_paths (path) VALUES (?)", [(p,) for p in paths])


def remove_excluded_paths(paths: list[str]):
    with DBConnection() as c:
        c.executemany("DELETE FROM excluded_paths WHERE path = ?", [(p,) for p in paths])


def get_excluded_paths() -> list[str]:
    with DBConnection() as c:
        c.execute("SELECT path FROM excluded_paths")
        return [row[0] for row in c.fetchall()]
