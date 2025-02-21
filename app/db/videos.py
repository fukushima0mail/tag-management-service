from PIL import Image
import os
import io

import ffmpeg
from sqlite3 import Binary, IntegrityError

from db.connection import DBConnection

def create_videos_table():
    with DBConnection() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                duration REAL,
                thumbnail BLOB,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

def add_video(file_path: str):
    """動画をデータベースに追加（再生時間は自動取得）"""
    if not os.path.exists(file_path):
        print(f"エラー: ファイルが見つかりません ({file_path})")
        return

    duration = get_video_duration(file_path)
    thumbnail = generate_thumbnail(file_path)
    with DBConnection() as c:
        try:
            c.execute("""
                INSERT INTO videos (file_path, duration, thumbnail) 
                VALUES (?, ?, ?)
            """, (file_path, duration, Binary(thumbnail)))
        except IntegrityError:
            print(f"既に登録されている動画です: {file_path}")

def get_all_videos():
    """登録されているすべての動画を取得"""
    with DBConnection() as c:
        c.execute("SELECT id, file_path, duration, thumbnail, added_at FROM videos")
        data = c.fetchall()

    print(f"Fetched {len(data)} videos from DB")

    result = []
    for video_id, file_path, duration, thumbnail, added_at in data:
        try:
            thumbnail_img = Image.open(io.BytesIO(thumbnail)) if thumbnail else None
        except Exception as e:
            print(f"エラー: サムネイルの読み込みに失敗 ({file_path})\n{e}")
            thumbnail_img = None  # サムネイルが壊れている場合は None にする

        result.append((video_id, file_path, duration, thumbnail_img, added_at))

    return result

def get_video_duration(file_path: str) -> float:
    """動画ファイルの再生時間を取得（秒単位）"""
    try:
        probe = ffmpeg.probe(file_path)
        duration = float(probe['format']['duration'])
        return duration
    except Exception as e:
        print(f"エラー: 動画の再生時間を取得できませんでした ({file_path})\n{e}")
        return 0.0

def generate_thumbnail(file_path: str) -> bytes:
    """ffmpegを使って動画のサムネイルを取得し、バイナリデータとして返す"""
    try:
        out, _ = (
            ffmpeg
            .input(file_path, ss=5)  # 5秒地点のフレームを取得
            .output("pipe:", vframes=1, format="image2", vcodec="mjpeg")
            .run(capture_stdout=True, capture_stderr=True)
        )
        return out  # JPEGのバイナリデータ
    except Exception as e:
        print(f"サムネイル生成エラー: {e}")
        return None
