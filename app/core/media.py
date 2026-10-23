import io
import os

from PIL import Image, ImageOps

try:
    import ffmpeg
except ImportError:
    ffmpeg = None

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp",
}
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
}
THUMBNAIL_SIZE = (320, 180)


def get_media_type(file_path: str) -> str | None:
    """拡張子からメディア種別（video / image）を判定"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return None


def extension_tag(file_path: str) -> str:
    """自動付与する拡張子タグ（例: mp4）"""
    return os.path.splitext(file_path)[1].lstrip(".").lower()


def is_same_or_under(path: str, directory: str) -> bool:
    """path が directory 自身、またはその配下か（大文字小文字を区別しない環境にも対応）"""
    path, directory = os.path.normcase(path), os.path.normcase(directory)
    return path == directory or path.startswith(os.path.join(directory, ""))


def iter_media_files(directory: str):
    """ディレクトリ配下（サブフォルダ含む）の動画・画像ファイルを列挙"""
    for root, _, files in os.walk(directory):
        for name in files:
            path = os.path.normpath(os.path.join(root, name))
            if get_media_type(path):
                yield path


def load_media_info(file_path: str) -> tuple[str, float | None, bytes | None]:
    """(メディア種別, 再生時間, サムネイルJPEG) を返す"""
    media_type = get_media_type(file_path)
    if media_type == "video":
        duration = get_video_duration(file_path)
        return media_type, duration, generate_video_thumbnail(file_path, duration)
    return media_type, None, generate_image_thumbnail(file_path)


def get_video_duration(file_path: str) -> float | None:
    """動画ファイルの再生時間を取得（秒単位）"""
    if ffmpeg is None:
        return None
    try:
        probe = ffmpeg.probe(file_path)
        return float(probe['format']['duration'])
    except Exception as e:
        print(f"エラー: 動画の再生時間を取得できませんでした ({file_path})\n{e}")
        return None


def generate_video_thumbnail(file_path: str, duration: float | None) -> bytes | None:
    """ffmpegで動画のフレームを取得し、サムネイルJPEGとして返す"""
    if ffmpeg is None:
        return None
    # 短い動画でも取得できるよう、5秒地点か動画の中間の早い方を使う
    position = min(5.0, duration / 2) if duration else 0
    try:
        out, _ = (
            ffmpeg
            .input(file_path, ss=position)
            .output("pipe:", vframes=1, format="image2", vcodec="mjpeg")
            .run(capture_stdout=True, capture_stderr=True)
        )
        if not out:
            return None
        return _to_thumbnail_bytes(Image.open(io.BytesIO(out)))
    except Exception as e:
        print(f"サムネイル生成エラー ({file_path}): {e}")
        return None


def generate_image_thumbnail(file_path: str) -> bytes | None:
    """画像ファイルからサムネイルJPEGを生成"""
    try:
        with Image.open(file_path) as img:
            return _to_thumbnail_bytes(ImageOps.exif_transpose(img))
    except Exception as e:
        print(f"サムネイル生成エラー ({file_path}): {e}")
        return None


def _to_thumbnail_bytes(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail(THUMBNAIL_SIZE)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
