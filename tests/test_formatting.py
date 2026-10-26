import pytest

from core.formatting import MAX_RATING, format_duration, format_rating, format_size, rating_stars


def test_rating_stars():
    assert rating_stars(3) == "★" * 3 + "☆" * (MAX_RATING - 3)


@pytest.mark.parametrize("rating, expected", [
    (0, "未評価"),
    (None, "未評価"),
    (10, "★" * 10 + " (10/10)"),
])
def test_format_rating(rating, expected):
    assert format_rating(rating) == expected


@pytest.mark.parametrize("seconds, expected", [
    (None, "-"),
    (0, "-"),
    (5.9, "0:05"),
    (65, "1:05"),
    (3725, "1:02:05"),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("size, expected", [
    (None, "-"),
    (0, "0 B"),
    (1023, "1023 B"),
    (1024, "1.0 KB"),
    (5 * 1024 ** 2, "5.0 MB"),
    (3 * 1024 ** 4, "3072.0 GB"),  # GB より大きい単位は使わない
])
def test_format_size(size, expected):
    assert format_size(size) == expected
