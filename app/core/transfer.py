"""タグ・評価のエクスポート／インポート（JSON）"""
import json
import os
from datetime import datetime

from core.formatting import MAX_RATING
from core.tags import normalize_tag
from db.transfer import apply_tags_and_ratings
from db.videos import get_all_videos

FORMAT_NAME = "tag-management-service"
FORMAT_VERSION = 1


def export_album(file_path: str, album_name: str) -> int:
    """現在のアルバムのタグ・評価を書き出し、件数を返す"""
    videos = get_all_videos()
    data = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "album": album_name,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "files": [
            {
                "path": v["file_path"],
                "size": v["file_size"],
                "modified_at": v["modified_at"],
                "rating": v["rating"] or 0,
                "tags": v["tags"],
            }
            for v in sorted(videos, key=lambda v: v["file_path"].lower())
        ],
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return len(videos)


def import_album(file_path: str) -> tuple[int, int]:
    """書き出したタグ・評価を現在のアルバムの登録済みファイルに反映し、(反映件数, 見つからなかった件数) を返す

    ファイルはパスで照合し、見つからなければサイズと更新日時が一致するもの（1件に絞れる場合のみ）を同じファイルとみなす
    （別のPCやドライブにコピーした場合など）。タグは追加のみで、既存のタグは消さない
    """
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        raise ValueError("このアプリで書き出したファイルではありません")

    videos = get_all_videos()
    by_path = {os.path.normcase(v["file_path"]): v["id"] for v in videos}
    by_stat: dict[tuple, list[int]] = {}
    for v in videos:
        if v["file_size"] is not None and v["modified_at"] is not None:
            by_stat.setdefault((v["file_size"], v["modified_at"]), []).append(v["id"])

    items, unmatched, used = [], 0, set()
    for entry in data.get("files", []):
        video_id = by_path.get(os.path.normcase(entry.get("path", "")))
        if video_id is None:
            candidates = [i for i in by_stat.get((entry.get("size"), entry.get("modified_at")), []) if i not in used]
            video_id = candidates[0] if len(candidates) == 1 else None
        if video_id is None:
            unmatched += 1
            continue
        used.add(video_id)
        rating = entry.get("rating") or 0
        rating = rating if isinstance(rating, int) and 0 <= rating <= MAX_RATING else 0
        tags = [t for t in (normalize_tag(str(t)) for t in entry.get("tags", [])) if t]
        items.append((video_id, rating, tags))

    apply_tags_and_ratings(items)
    return len(items), unmatched
