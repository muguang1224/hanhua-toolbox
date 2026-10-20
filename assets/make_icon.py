"""生成 assets/icon.ico：蓝底圆角方块 + 白色「汉」字。

    python assets/make_icon.py            # 生成 icon.ico
    python assets/make_icon.py --preview  # 额外输出一张 256/64/32/16 的拼图，方便看效果

每个尺寸单独渲染再塞进 ICO，比"画大图再整体缩小"的小尺寸更清楚。
"""

from __future__ import annotations

import io
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ICO = os.path.join(HERE, "icon.ico")
OUT_PREVIEW = os.path.join(HERE, "icon_preview.png")

SIZES = [16, 24, 32, 48, 64, 128, 256]
GLYPH = "汉"

BLUE_TOP = (59, 123, 245)      # #3B7BF5
BLUE_BOTTOM = (39, 96, 216)    # #2760D8

FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", range(0, 6)),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", range(0, 6)),
    ("C:/Windows/Fonts/msyhbd.ttc", range(0, 2)),
    ("C:/Windows/Fonts/msyh.ttc", range(0, 2)),
]


def load_font(px: int) -> ImageFont.FreeTypeFont:
    for path, indices in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        for idx in indices:
            try:
                font = ImageFont.truetype(path, px, index=idx)
            except OSError:
                continue
            # 有些 ttc 子集没有这个字，渲染出来宽度为 0
            if font.getbbox(GLYPH)[2] > 0:
                return font
    raise SystemExit("找不到能渲染「%s」的字体，检查 FONT_CANDIDATES" % GLYPH)


def rounded_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return mask


def render(size: int) -> Image.Image:
    ss = 4  # 超采样，边缘更干净
    big = size * ss

    bg = Image.new("RGB", (big, big))
    draw = ImageDraw.Draw(bg)
    for y in range(big):  # 竖向渐变
        t = y / (big - 1)
        draw.line([(0, y), (big, y)], fill=tuple(
            round(BLUE_TOP[i] + (BLUE_BOTTOM[i] - BLUE_TOP[i]) * t) for i in range(3)))

    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(bg, (0, 0), rounded_mask(big, big * 0.22))

    # 小尺寸让字占得更满，否则 16px 下认不出来
    ratio = 0.74 if size <= 32 else 0.68
    font = load_font(int(big * ratio))
    draw2 = ImageDraw.Draw(img)

    # 用实际绘制出的 bbox 居中，比按字号估算准（中文字形重心偏下）
    l, t, r, b = draw2.textbbox((0, 0), GLYPH, font=font)
    draw2.text(((big - (r + l)) / 2, (big - (b + t)) / 2), GLYPH, font=font, fill="white")

    return img.resize((size, size), Image.LANCZOS)


def write_ico(images: list[Image.Image]) -> None:
    payloads = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        payloads.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory = b""
    for img, data in zip(images, payloads):
        w = 0 if img.width >= 256 else img.width   # 0 表示 256
        h = 0 if img.height >= 256 else img.height
        directory += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    with open(OUT_ICO, "wb") as f:
        f.write(header + directory + b"".join(payloads))


def write_preview(images: list[Image.Image]) -> None:
    pad, gap = 16, 24
    shown = [im for im in images if im.width in (256, 64, 32, 16)]
    width = sum(im.width for im in shown) + gap * (len(shown) + 1)
    height = max(im.height for im in shown) + pad * 2
    canvas = Image.new("RGBA", (width, height), (244, 245, 247, 255))
    x = gap
    for im in shown:
        canvas.paste(im, (x, (height - im.height) // 2), im)
        x += im.width + gap
    canvas.save(OUT_PREVIEW)


def main() -> None:
    images = [render(s) for s in SIZES]
    write_ico(images)
    print(f"已生成 {OUT_ICO}（{len(SIZES)} 个尺寸：{', '.join(map(str, SIZES))}）")
    if "--preview" in sys.argv:
        write_preview(images)
        print(f"预览图：{OUT_PREVIEW}")


if __name__ == "__main__":
    main()
