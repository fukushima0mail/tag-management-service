from collections import defaultdict

from .connection import DBConnection


def create_tags_table():
    with DBConnection() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)

def create_video_tags_table():
    with DBConnection() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS video_tags (
                video_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (video_id, tag_id)
            )
        """)

def add_tag(video_ids: list[int], name: str):
    """指定した動画にタグを付与（タグが未登録なら作成）"""
    with DBConnection() as c:
        c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        c.execute("SELECT id FROM tags WHERE name = ?", (name,))
        tag_id = c.fetchone()[0]
        c.executemany(
            "INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
            [(video_id, tag_id) for video_id in video_ids],
        )

def remove_tag(video_ids: list[int], name: str):
    """指定した動画からタグを外す"""
    with DBConnection() as c:
        c.executemany(
            "DELETE FROM video_tags WHERE video_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)",
            [(video_id, name) for video_id in video_ids],
        )
        delete_unused_tags(c)

def delete_unused_tags(c):
    """どの動画にも付いていないタグを削除"""
    c.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM video_tags)")

def get_tags_by_video() -> dict[int, list[str]]:
    """video_id -> タグ名一覧"""
    with DBConnection() as c:
        c.execute("""
            SELECT vt.video_id, t.name
            FROM video_tags vt JOIN tags t ON t.id = vt.tag_id
            ORDER BY t.name
        """)
        result = defaultdict(list)
        for video_id, name in c.fetchall():
            result[video_id].append(name)
    return result
