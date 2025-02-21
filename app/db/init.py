from db.videos import create_videos_table
from db.tags import create_tags_table, create_video_tags_table

def init_table():
    create_videos_table()
    create_tags_table()
    create_video_tags_table()
    print("テーブルの初期化が完了しました")
