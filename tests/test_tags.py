import pytest

from core.tags import normalize_tag, parse_tags, split_tag


@pytest.mark.parametrize("name, expected", [
    ("年代:2020", ("年代", "2020")),
    (" 年代 : 2020 ", ("年代", "2020")),
    ("旅行", (None, "旅行")),
    (":2020", (None, ":2020")),     # グループ名が空ならグループなし
    ("年代:", (None, "年代:")),     # 値が空ならグループなし
    ("a:b:c", ("a", "b:c")),        # 最初の「:」で分ける
])
def test_split_tag(name, expected):
    assert split_tag(name) == expected


@pytest.mark.parametrize("name, expected", [
    ("年代：2020", "年代:2020"),
    ("年代 : 2020", "年代:2020"),
    ("  旅行  ", "旅行"),
    ("", ""),
])
def test_normalize_tag(name, expected):
    assert normalize_tag(name) == expected


def test_parse_tags_splits_commas_and_removes_duplicates():
    assert parse_tags("旅行, 年代：2020、旅行,, ") == ["旅行", "年代:2020"]


def test_parse_tags_empty():
    assert parse_tags("") == []
