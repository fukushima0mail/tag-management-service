from db.connection import DBConnection


def apply_tags_and_ratings(items: list[tuple[int, int, list[str]]]):
    """(video_id, 評価, タグ一覧) をまとめて反映する（タグは追加のみ、評価は 0 以外なら上書き）"""
    with DBConnection() as c:
        for video_id, rating, tags in items:
            if rating:
                c.execute("UPDATE videos SET rating = ? WHERE id = ?", (rating, video_id))
            for name in tags:
                c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                c.execute(
                    "INSERT OR IGNORE INTO video_tags (video_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
                    (video_id, name),
                )
