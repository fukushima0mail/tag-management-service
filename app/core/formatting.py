from datetime import datetime

MAX_RATING = 10


def rating_stars(rating: int) -> str:
    return "★" * rating + "☆" * (MAX_RATING - rating)


def format_rating(rating: int | None) -> str:
    return f"{rating_stars(rating)} ({rating}/{MAX_RATING})" if rating else "未評価"


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{sec:02}" if hours else f"{minutes}:{sec:02}"


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def format_mtime(mtime: float | None) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "-"
