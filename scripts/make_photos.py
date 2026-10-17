"""Render SYNTHETIC photos of the CL-204 snap clip: a golden sample and three defect photos.
Each image carries a small "SYNTHETIC" watermark. Replace them with real phone photos for a stronger demo,
and keep the same file names. Defect boxes ([ymin, xmin, ymax, xmax], 0-1000) are printed for the case catalog."""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1024, 768
OUT = Path(__file__).resolve().parents[1] / "web" / "photos"
PLASTIC = (58, 63, 71)
EDGE = (40, 44, 50)
HILITE = (96, 104, 116)


def base(seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            g = 196 - int(40 * (y / H)) - int(18 * abs(x - W / 2) / W)
            n = rnd.randint(-6, 6)
            px[x, y] = (g + n, g + 4 + n, g + 10 + n)
    return img


def clip(d: ImageDraw.ImageDraw) -> None:
    d.rounded_rectangle([260, 470, 760, 580], radius=22, fill=PLASTIC, outline=EDGE, width=4)    # body
    d.rectangle([470, 250, 520, 480], fill=PLASTIC, outline=EDGE, width=4)                       # stem
    d.rounded_rectangle([470, 220, 640, 270], radius=10, fill=PLASTIC, outline=EDGE, width=4)    # hook arm
    d.rounded_rectangle([600, 220, 640, 330], radius=8, fill=PLASTIC, outline=EDGE, width=4)     # tooth
    d.line([(272, 482), (748, 482)], fill=HILITE, width=3)                                         # specular edge
    d.line([(476, 262), (476, 470)], fill=HILITE, width=2)


def finish(img: Image.Image, seed: int) -> Image.Image:
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    rnd = random.Random(seed + 1)
    px = img.load()
    for _ in range(9000):  # sensor noise
        x, y = rnd.randrange(W), rnd.randrange(H)
        r, g, b = px[x, y]
        n = rnd.randint(-12, 12)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    d.text((W - 150, H - 32), "SYNTHETIC", fill=(120, 126, 134), font=font)
    return img


def golden(seed=1):
    img = base(seed)
    clip(ImageDraw.Draw(img))
    return finish(img, seed)


def crack(seed=2):
    img = base(seed)
    d = ImageDraw.Draw(img)
    clip(d)
    pts = [(486, 466), (494, 452), (489, 440), (499, 428), (493, 415), (503, 404)]
    d.line(pts, fill=(170, 176, 184), width=7)   # stress whitening
    d.line(pts, fill=(20, 22, 26), width=3)      # crack
    return finish(img, seed), [round(390 / H * 1000), round(470 / W * 1000), round(480 / H * 1000), round(520 / W * 1000)]


def burr(seed=3):
    img = base(seed)
    d = ImageDraw.Draw(img)
    clip(d)
    for i, y in enumerate(range(236, 326, 6)):
        d.polygon([(640, y), (652 + (i % 3) * 3, y + 2), (640, y + 5)], fill=(150, 156, 166))  # thin flash
    return finish(img, seed), [round(225 / H * 1000), round(630 / W * 1000), round(335 / H * 1000), round(670 / W * 1000)]


def scratch(seed=4):
    img = base(seed)
    d = ImageDraw.Draw(img)
    clip(d)
    for x0, y0, x1, y1 in [(320, 520, 420, 540), (340, 530, 455, 548), (600, 505, 690, 520)]:
        d.line([(x0, y0), (x1, y1)], fill=(130, 136, 146), width=2)
    return finish(img, seed), [round(495 / H * 1000), round(305 / W * 1000), round(560 / H * 1000), round(700 / W * 1000)]


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    golden().save(OUT / "golden_cl204.png", optimize=True)
    for name, fn in (("case118_defect.png", crack), ("case124_defect.png", burr), ("case121_defect.png", scratch)):
        img, box = fn()
        img.save(OUT / name, optimize=True)
        print(name, "box_2d", box)
