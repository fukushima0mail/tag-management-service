from db.videos import create_videos_table
from db.tags import create_tags_table, create_video_tags_table
from db.exclusions import create_excluded_paths_table

def init_table():
    create_videos_table()
    create_tags_table()
    create_video_tags_table()
    create_excluded_paths_table()
    print("テーブルの初期化が完了しました")
