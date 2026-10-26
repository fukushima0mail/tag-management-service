import os

from db.albums import (
    DEFAULT_ALBUM_NAME, create_album, get_directories, list_albums, rename_album, set_directories,
)
from db.exclusions import add_excluded_paths, get_excluded_paths, remove_excluded_paths
from db.tags import add_tag, migrate_extension_tags, remove_tags, rename_tags
from db.videos import add_video, delete_videos, get_all_videos, get_thumbnails, get_video_index, rename_video, set_rating
from core.media import extension_tag


def _add(path: str, thumbnail: bytes | None = None) -> int:
    return add_video(path, "image", None, thumbnail, 100, 1.0)


def _tags(video_id: int) -> list[str]:
    return next(v["tags"] for v in get_all_videos() if v["id"] == video_id)


# ---------------------------------------------------------------- videos
def test_add_video_upserts_by_path(album):
    first = _add("C:/m/a.jpg")
    again = add_video("C:/m/a.jpg", "image", None, None, 200, 2.0)
    assert first == again
    assert get_video_index() == {"C:/m/a.jpg": (first, 200, 2.0)}


def test_rating_and_rename(album):
    video_id = _add("C:/m/a.jpg")
    set_rating([video_id], 8)
    rename_video(video_id, "C:/m/b.jpg")
    [video] = get_all_videos()
    assert (video["file_path"], video["rating"]) == ("C:/m/b.jpg", 8)


def test_get_thumbnails_skips_missing(album):
    with_thumb = _add("C:/m/a.jpg", b"jpeg")
    without = _add("C:/m/b.jpg")
    assert get_thumbnails([with_thumb, without]) == {with_thumb: b"jpeg"}


def test_delete_videos_removes_unused_tags(album):
    a, b = _add("C:/m/a.jpg"), _add("C:/m/b.jpg")
    add_tag([a], "aだけ")
    add_tag([a, b], "共通")
    delete_videos([a])
    assert _tags(b) == ["共通"]
    add_tag([b], "aだけ")  # 削除済みタグも作り直せる
    assert _tags(b) == ["aだけ", "共通"]


# ---------------------------------------------------------------- tags
def test_add_and_remove_tags(album):
    a, b = _add("C:/m/a.jpg"), _add("C:/m/b.jpg")
    add_tag([a, b], "x")
    add_tag([a], "y")
    add_tag([a], "y")  # 重複は無視
    remove_tags([a, b], ["x", "存在しない"])
    assert _tags(a) == ["y"]
    assert _tags(b) == []


def test_rename_tags_merges_into_existing(album):
    a, b = _add("C:/m/a.jpg"), _add("C:/m/b.jpg")
    add_tag([a], "old")
    add_tag([a, b], "new")
    rename_tags({"old": "new", "missing": "whatever"})
    assert _tags(a) == ["new"]
    assert _tags(b) == ["new"]


def test_rename_tags_simple(album):
    a = _add("C:/m/a.jpg")
    add_tag([a], "2020")
    rename_tags({"2020": "年代:2020"})
    assert _tags(a) == ["年代:2020"]


def test_migrate_extension_tags(album):
    a = _add("C:/m/a.JPG")
    add_tag([a], "jpg")      # 旧形式の拡張子タグ
    add_tag([a], "png")      # 拡張子と一致しないタグは対象外
    assert migrate_extension_tags(extension_tag) == 1
    assert _tags(a) == ["png", "拡張子:jpg"]
    assert migrate_extension_tags(extension_tag) == 0  # 2回目は何もしない


# ---------------------------------------------------------------- exclusions
def test_excluded_paths(album):
    add_excluded_paths(["C:/m/a.jpg", "C:/m/sub", "C:/m/a.jpg"])
    remove_excluded_paths(["C:/m/sub"])
    assert get_excluded_paths() == ["C:/m/a.jpg"]


# ---------------------------------------------------------------- albums
def test_albums_are_separate_dbs(album):
    _add("C:/m/a.jpg")
    set_directories(["C:/m"])
    other = create_album("もう1つ")

    assert [name for _, name in list_albums()] == ["テスト", "もう1つ"]
    assert all(os.path.dirname(path) == "albums" for path, _ in list_albums())

    from db.connection import set_db_path
    from db.init import init_table
    set_db_path(other)
    init_table()
    assert get_all_videos() == []
    assert get_directories() == []


def test_rename_album(album):
    rename_album("新しい名前")
    assert list_albums() == [(album, "新しい名前")]


def test_album_without_name_uses_default(album):
    from db.connection import DBConnection
    with DBConnection() as c:
        c.execute("DELETE FROM album_meta WHERE key = 'name'")
    assert list_albums() == [(album, DEFAULT_ALBUM_NAME)]


def test_create_album_avoids_name_collision(album):
    paths = {create_album("a") for _ in range(3)}
    assert len(paths) == 3


def test_ignores_db_outside_albums_dir(album):
    open("video.db", "wb").close()
    assert [path for path, _ in list_albums()] == [album]
