import json
import os

import pytest

from core.transfer import export_album, import_album
from db.tags import add_tag
from db.videos import add_video, get_all_videos, set_rating


def _add(path: str, size: int = 100, mtime: float = 1.0) -> int:
    return add_video(path, "image", None, None, size, mtime)


def _video(video_id: int) -> dict:
    return next(v for v in get_all_videos() if v["id"] == video_id)


def _clear(video_id: int):
    from db.tags import remove_tags
    remove_tags([video_id], _video(video_id)["tags"])
    set_rating([video_id], 0)


def test_export_contents(album, tmp_path):
    a = _add("C:/m/a.jpg")
    add_tag([a], "旅行")
    set_rating([a], 4)
    out = tmp_path / "out.json"
    assert export_album(str(out), "テスト") == 1

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["album"] == "テスト"
    assert data["files"] == [
        {"path": "C:/m/a.jpg", "size": 100, "modified_at": 1.0, "rating": 4, "tags": ["旅行"]},
    ]


def test_roundtrip_restores_tags_and_rating(album, tmp_path):
    a = _add("C:/m/a.jpg")
    add_tag([a], "旅行")
    add_tag([a], "年代:2020")
    set_rating([a], 7)
    out = str(tmp_path / "out.json")
    export_album(out, "テスト")

    _clear(a)
    assert import_album(out) == (1, 0)
    video = _video(a)
    assert sorted(video["tags"]) == ["年代:2020", "旅行"]
    assert video["rating"] == 7


def test_import_merges_tags_and_keeps_rating_when_unrated(album, tmp_path):
    a = _add("C:/m/a.jpg")
    add_tag([a], "書き出し時")
    out = str(tmp_path / "out.json")
    export_album(out, "テスト")  # 評価なし

    add_tag([a], "後から")
    set_rating([a], 5)
    import_album(out)
    video = _video(a)
    assert sorted(video["tags"]) == ["後から", "書き出し時"]  # 既存タグは消さない
    assert video["rating"] == 5                              # 未評価で上書きしない


def test_import_matches_by_size_and_mtime_when_path_differs(album, tmp_path):
    """別のPC・ドライブにコピーしてパスが変わった場合"""
    a = _add("D:/old/a.jpg", size=111, mtime=5.0)
    add_tag([a], "移行")
    out = str(tmp_path / "out.json")
    export_album(out, "テスト")

    from db.videos import delete_videos
    delete_videos([a])
    b = _add("E:/new/renamed.jpg", size=111, mtime=5.0)
    _add("E:/new/other.jpg", size=222, mtime=5.0)
    assert import_album(out) == (1, 0)
    assert _video(b)["tags"] == ["移行"]


def test_import_skips_ambiguous_and_unknown(album, tmp_path):
    out = tmp_path / "in.json"
    out.write_text(json.dumps({
        "format": "tag-management-service", "version": 1,
        "files": [
            {"path": "Z:/none.jpg", "size": 1, "modified_at": 1.0, "rating": 3, "tags": ["x"]},
            {"path": "Z:/dup.jpg", "size": 100, "modified_at": 1.0, "rating": 3, "tags": ["x"]},
        ],
    }), encoding="utf-8")
    a, b = _add("C:/m/a.jpg"), _add("C:/m/b.jpg")  # 同じサイズ・更新日時が2件 → 特定できない
    assert import_album(str(out)) == (0, 2)
    assert _video(a)["tags"] == [] and _video(b)["tags"] == []


def test_import_normalizes_and_validates(album, tmp_path):
    a = _add("C:/m/a.jpg")
    out = tmp_path / "in.json"
    out.write_text(json.dumps({
        "format": "tag-management-service", "version": 1,
        "files": [{"path": os.path.normcase("C:/m/A.JPG") if os.name == "nt" else "C:/m/a.jpg",
                   "rating": 99, "tags": ["年代：2020", " "]}],
    }), encoding="utf-8")
    assert import_album(str(out)) == (1, 0)
    video = _video(a)
    assert video["tags"] == ["年代:2020"]
    assert video["rating"] == 0  # 範囲外の評価は無視


@pytest.mark.parametrize("content", ['{"files": []}', "[]", "not json"])
def test_import_rejects_other_files(album, tmp_path, content):
    out = tmp_path / "in.json"
    out.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        import_album(str(out))
