from db.connection import DBConnection
from db.tags import delete_unused_tags, get_tags_by_video


def create_videos_table():
    with DBConnection() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                duration REAL,
                thumbnail BLOB,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                media_type TEXT NOT NULL DEFAULT 'video',
                file_size INTEGER,
                modified_at REAL
            )
        """)

        # 既存DBに不足しているカラムを追加
        c.execute("PRAGMA table_info(videos)")
        columns = {row[1] for row in c.fetchall()}
        for name, definition in (
            ("media_type", "TEXT NOT NULL DEFAULT 'video'"),
            ("file_size", "INTEGER"),
            ("modified_at", "REAL"),
        ):
            if name not in columns:
                c.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")


def add_video(file_path: str, media_type: str, duration: float | None, thumbnail: bytes | None,
              file_size: int, modified_at: float) -> int:
    """動画・画像を登録（登録済みなら更新）し、IDを返す"""
    with DBConnection() as c:
        c.execute("""
            INSERT INTO videos (file_path, media_type, duration, thumbnail, file_size, modified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                media_type = excluded.media_type,
                duration = excluded.duration,
                thumbnail = excluded.thumbnail,
                file_size = excluded.file_size,
                modified_at = excluded.modified_at
            RETURNING id
        """, (file_path, media_type, duration, thumbnail, file_size, modified_at))
        return c.fetchone()[0]


def delete_videos(video_ids: list[int]):
    if not video_ids:
        return
    with DBConnection() as c:
        c.executemany("DELETE FROM videos WHERE id = ?", [(v,) for v in video_ids])
        delete_unused_tags(c)


def get_video_index() -> dict[str, tuple[int, int | None, float | None]]:
    """file_path -> (id, file_size, modified_at)"""
    with DBConnection() as c:
        c.execute("SELECT id, file_path, file_size, modified_at FROM videos")
        return {path: (video_id, size, mtime) for video_id, path, size, mtime in c.fetchall()}


def get_all_videos() -> list[dict]:
    """登録されているすべての動画・画像をタグ付きで取得（サムネイルは含まない）"""
    with DBConnection() as c:
        c.execute("""
            SELECT id, file_path, media_type, duration, file_size, modified_at, added_at
            FROM videos
        """)
        rows = c.fetchall()

    tags = get_tags_by_video()
    return [
        {
            "id": video_id,
            "file_path": file_path,
            "media_type": media_type,
            "duration": duration,
            "file_size": file_size,
            "modified_at": modified_at,
            "added_at": added_at,
            "tags": tags.get(video_id, []),
        }
        for video_id, file_path, media_type, duration, file_size, modified_at, added_at in rows
    ]


def get_thumbnails(video_ids: list[int]) -> dict[int, bytes]:
    """video_id -> サムネイルJPEG"""
    result = {}
    with DBConnection() as c:
        for i in range(0, len(video_ids), 500):
            chunk = video_ids[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            c.execute(
                f"SELECT id, thumbnail FROM videos WHERE id IN ({placeholders}) AND thumbnail IS NOT NULL",
                chunk,
            )
            result.update(c.fetchall())
    return result
