"""アプリアイコン（app/assets/icon.ico, icon.png）を生成する

    python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

SCALE = 4  # 4倍の大きさで描いてから縮小し、輪郭を滑らかにする
SIZE = 256 * SCALE
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "assets")

BG_TOP = (79, 70, 229)      # インディゴ
BG_BOTTOM = (37, 99, 235)   # ブルー
CARD = (255, 255, 255)
PLAY = (79, 70, 229)
TAG = (245, 158, 11)        # オレンジ
TAG_EDGE = (255, 255, 255)


def s(v: float) -> int:
    return int(v * SCALE)


def background() -> Image.Image:
    gradient = Image.new("RGB", (SIZE, SIZE))
    draw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        draw.line([(0, y), (SIZE, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([s(8), s(8), s(248), s(248)], radius=s(52), fill=255)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)
    return icon


def thumbnail_card(icon: Image.Image):
    """再生マーク付きのサムネイルカード"""
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle([s(36), s(58), s(196), s(168)], radius=s(16), fill=CARD)
    draw.polygon([(s(98), s(84)), (s(98), s(142)), (s(146), s(113))], fill=PLAY)


def tag(icon: Image.Image):
    """右下に重ねる値札型のタグ（斜め）"""
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = s(170), s(176)
    w, h = s(104), s(58)
    left, right, top, bottom = cx - w // 2, cx + w // 2, cy - h // 2, cy + h // 2
    shape = [(left, cy), (left + h // 2, top), (right, top), (right, bottom), (left + h // 2, bottom)]
    draw.polygon(shape, fill=TAG_EDGE)  # 縁取り（カードとの境目をはっきりさせる）
    inset = s(7)
    draw.polygon([
        (left + inset * 1.4, cy), (left + h // 2 + inset * 0.4, top + inset), (right - inset, top + inset),
        (right - inset, bottom - inset), (left + h // 2 + inset * 0.4, bottom - inset),
    ], fill=TAG)
    hole = s(9)
    hx = left + h // 2 + s(2)
    draw.ellipse([hx - hole, cy - hole, hx + hole, cy + hole], fill=TAG_EDGE)
    layer = layer.rotate(35, center=(cx, cy), resample=Image.Resampling.BICUBIC)
    icon.alpha_composite(layer)


def main():
    icon = background()
    thumbnail_card(icon)
    tag(icon)
    icon = icon.resize((256, 256), Image.Resampling.LANCZOS)

    os.makedirs(OUT_DIR, exist_ok=True)
    icon.save(os.path.join(OUT_DIR, "icon.png"))
    icon.save(os.path.join(OUT_DIR, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"作成しました: {os.path.abspath(OUT_DIR)}")


if __name__ == "__main__":
    main()
