from core.media import DEFAULT_THUMBNAIL_WIDTH
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
                modified_at REAL,
                rating INTEGER NOT NULL DEFAULT 0,
                thumbnail_width INTEGER
            )
        """)

        # 既存DBに不足しているカラムを追加
        c.execute("PRAGMA table_info(videos)")
        columns = {row[1] for row in c.fetchall()}
        for name, definition in (
            ("media_type", "TEXT NOT NULL DEFAULT 'video'"),
            ("file_size", "INTEGER"),
            ("modified_at", "REAL"),
            ("rating", "INTEGER NOT NULL DEFAULT 0"),
            ("thumbnail_width", "INTEGER"),  # サムネイルを作ったときの幅（NULL は既定の幅）
        ):
            if name not in columns:
                c.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")


def add_video(file_path: str, media_type: str, duration: float | None, thumbnail: bytes | None,
              file_size: int, modified_at: float, thumbnail_width: int = DEFAULT_THUMBNAIL_WIDTH) -> int:
    """動画・画像を登録（登録済みなら更新）し、IDを返す"""
    with DBConnection() as c:
        c.execute("""
            INSERT INTO videos (file_path, media_type, duration, thumbnail, file_size, modified_at, thumbnail_width)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                media_type = excluded.media_type,
                duration = excluded.duration,
                thumbnail = excluded.thumbnail,
                file_size = excluded.file_size,
                modified_at = excluded.modified_at,
                thumbnail_width = excluded.thumbnail_width
            RETURNING id
        """, (file_path, media_type, duration, thumbnail, file_size, modified_at, thumbnail_width))
        return c.fetchone()[0]


def get_outdated_thumbnail_ids(width: int) -> set[int]:
    """サムネイルの幅が width と異なる（作り直しが必要な）ファイルのID"""
    with DBConnection() as c:
        c.execute("SELECT id FROM videos WHERE COALESCE(thumbnail_width, ?) != ?", (DEFAULT_THUMBNAIL_WIDTH, width))
        return {row[0] for row in c.fetchall()}


def delete_videos(video_ids: list[int]):
    if not video_ids:
        return
    with DBConnection() as c:
        c.executemany("DELETE FROM videos WHERE id = ?", [(v,) for v in video_ids])
        delete_unused_tags(c)


def set_rating(video_ids: list[int], rating: int):
    """評価（0: 未評価, 1〜10）を設定"""
    with DBConnection() as c:
        c.executemany("UPDATE videos SET rating = ? WHERE id = ?", [(rating, v) for v in video_ids])


def rename_video(video_id: int, file_path: str):
    with DBConnection() as c:
        c.execute("UPDATE videos SET file_path = ? WHERE id = ?", (file_path, video_id))


def get_video_index() -> dict[str, tuple[int, int | None, float | None]]:
    """file_path -> (id, file_size, modified_at)"""
    with DBConnection() as c:
        c.execute("SELECT id, file_path, file_size, modified_at FROM videos")
        return {path: (video_id, size, mtime) for video_id, path, size, mtime in c.fetchall()}


def get_all_videos() -> list[dict]:
    """登録されているすべての動画・画像をタグ付きで取得（サムネイルは含まない）"""
    with DBConnection() as c:
        c.execute("""
            SELECT id, file_path, media_type, duration, file_size, modified_at, added_at, rating
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
            "rating": rating,
            "tags": tags.get(video_id, []),
        }
        for video_id, file_path, media_type, duration, file_size, modified_at, added_at, rating in rows
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
