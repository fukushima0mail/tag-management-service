import os
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

def remove_tags(video_ids: list[int], names: list[str]):
    """指定した動画からタグを外す"""
    with DBConnection() as c:
        c.executemany(
            "DELETE FROM video_tags WHERE video_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)",
            [(video_id, name) for video_id in video_ids for name in names],
        )
        delete_unused_tags(c)

def rename_tags(renames: dict[str, str]):
    """タグ名を一括変更（変更先のタグが既にある場合は統合する）"""
    with DBConnection() as c:
        for old, new in renames.items():
            if old == new:
                continue
            c.execute("SELECT id FROM tags WHERE name = ?", (old,))
            row = c.fetchone()
            if row is None:
                continue
            old_id = row[0]
            c.execute("SELECT id FROM tags WHERE name = ?", (new,))
            row = c.fetchone()
            if row is None:
                c.execute("UPDATE tags SET name = ? WHERE id = ?", (new, old_id))
            else:
                c.execute(
                    "INSERT OR IGNORE INTO video_tags (video_id, tag_id) SELECT video_id, ? FROM video_tags WHERE tag_id = ?",
                    (row[0], old_id),
                )
                c.execute("DELETE FROM tags WHERE id = ?", (old_id,))  # video_tags は ON DELETE CASCADE で削除

def delete_tags(names: list[str]) -> int:
    """タグをすべてのファイルから外して削除し、外した件数（ファイル×タグ）を返す"""
    with DBConnection() as c:
        placeholders = ",".join("?" * len(names))
        c.execute(
            f"SELECT COUNT(*) FROM video_tags WHERE tag_id IN (SELECT id FROM tags WHERE name IN ({placeholders}))",
            names,
        )
        count = c.fetchone()[0]
        c.execute(f"DELETE FROM tags WHERE name IN ({placeholders})", names)  # video_tags は ON DELETE CASCADE
    return count


def migrate_extension_tags(extension_tag):
    """旧形式の拡張子タグ（例: jpg）を新形式（例: 拡張子:jpg）に置き換える

    ファイル自身の拡張子と同じ名前のタグだけを対象にする（何度実行しても結果は同じ）
    """
    with DBConnection() as c:
        c.execute("""
            SELECT v.id, v.file_path, t.id, t.name
            FROM videos v
            JOIN video_tags vt ON vt.video_id = v.id
            JOIN tags t ON t.id = vt.tag_id
        """)
        targets = [
            (video_id, tag_id, extension_tag(path))
            for video_id, path, tag_id, name in c.fetchall()
            if "." + name.lower() == os.path.splitext(path)[1].lower()
        ]
        for video_id, tag_id, new_name in targets:
            c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (new_name,))
            c.execute(
                "INSERT OR IGNORE INTO video_tags (video_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
                (video_id, new_name),
            )
            c.execute("DELETE FROM video_tags WHERE video_id = ? AND tag_id = ?", (video_id, tag_id))
        delete_unused_tags(c)
    return len(targets)

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
