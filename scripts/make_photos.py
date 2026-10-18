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


if __name__ == "__main__" and "--parts" not in __import__("sys").argv:
    OUT.mkdir(parents=True, exist_ok=True)
    golden().save(OUT / "golden_cl204.png", optimize=True)
    for name, fn in (("case118_defect.png", crack), ("case124_defect.png", burr), ("case121_defect.png", scratch)):
        img, box = fn()
        img.save(OUT / name, optimize=True)
        print(name, "box_2d", box)


# ------------------------------------------------------------------ the other three parts of the demo plant
# python scripts/make_photos.py --parts renders only these, so the CL-204 photos above are left as they are.
import math  # noqa: E402

PBT = (205, 199, 182)
PBT_EDGE = (150, 144, 128)
PP = (40, 42, 47)
PP_EDGE = (22, 24, 28)
POM = (232, 230, 222)
POM_EDGE = (168, 166, 158)


def housing(d: ImageDraw.ImageDraw, short: bool = False) -> None:
    """HX-310 connector housing: a block with eight terminal cavities and a latch ramp on top."""
    if short:  # the top right corner never filled: the melt froze before it got there
        flow = [(650 + 74 * t, 300 + 72 * t ** 0.8 + 9 * math.sin(t * 9)) for t in [i / 12 for i in range(13)]]
        body = [(310, 300), *flow, (724, 510), (714, 520), (310, 520), (300, 510), (300, 310)]
        d.polygon(body, fill=PBT, outline=PBT_EDGE)
        d.line(flow, fill=(176, 168, 148), width=5)   # the rounded, glossy flow front
    else:
        d.rounded_rectangle([300, 300, 724, 520], radius=10, fill=PBT, outline=PBT_EDGE, width=4)
    d.polygon([(440, 300), (584, 300), (560, 250), (464, 250)], fill=PBT, outline=PBT_EDGE)
    for row, y in enumerate((340, 430)):
        for i in range(4):
            x = 340 + i * 92
            d.rectangle([x, y, x + 60, y + 50], fill=(70, 66, 58), outline=(120, 114, 100), width=2)
    d.line([(312, 308), (640, 308)], fill=(226, 222, 208), width=3)


def cover(d: ImageDraw.ImageDraw, flash: bool = False) -> None:
    """BC-115 battery cover: a plate with grip ridges and a snap tab on the right."""
    d.rounded_rectangle([270, 280, 754, 540], radius=40, fill=PP, outline=PP_EDGE, width=4)
    d.rounded_rectangle([740, 370, 800, 450], radius=8, fill=PP, outline=PP_EDGE, width=3)
    for i in range(6):
        d.line([(320 + i * 14, 360), (320 + i * 14, 460)], fill=(70, 74, 80), width=4)
    d.line([(300, 292), (720, 292)], fill=(84, 88, 96), width=3)
    if flash:  # thin fins of plastic squeezed out along the parting line
        for i, y in enumerate(range(372, 448, 7)):
            d.polygon([(800, y), (811 + (i % 3) * 4, y + 2), (800, y + 5)], fill=(128, 132, 140))
        for i, y in enumerate(range(300, 366, 7)):
            d.polygon([(754, y), (764 + (i % 2) * 4, y + 2), (754, y + 5)], fill=(118, 122, 130))


def gear(d: ImageDraw.ImageDraw, crack: bool = False) -> None:
    """GR-88 window regulator gear: 24 teeth, a hub bore with a keyway."""
    cx, cy, ro, rr, n = 512, 400, 200, 172, 24
    pts = []
    for k in range(n):
        a0 = 2 * math.pi * k / n
        for da, r in ((0.0, rr), (0.06, ro), (0.15, ro), (0.21, rr)):
            a = a0 + da
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=POM, outline=POM_EDGE)
    d.ellipse([cx - 120, cy - 120, cx + 120, cy + 120], outline=(206, 204, 196), width=3)
    d.ellipse([cx - 58, cy - 58, cx + 58, cy + 58], fill=(92, 96, 104), outline=POM_EDGE, width=3)
    d.rectangle([cx - 10, cy - 70, cx + 10, cy - 52], fill=(92, 96, 104))
    if crack:  # a brittle crack running in from a tooth root
        th = math.radians(-50)
        line = [(cx + r * math.cos(th) + j, cy + r * math.sin(th) + j / 2)
                for r, j in ((176, 0), (166, 4), (156, -3), (146, 4), (136, -2), (126, 3))]
        d.line(line, fill=(250, 250, 246), width=7)
        d.line(line, fill=(44, 44, 48), width=4)


def _photo(seed, draw, **kw):
    img = base(seed)
    draw(ImageDraw.Draw(img), **kw)
    return finish(img, seed)


def _box(y0, x0, y1, x1):
    return [round(y0 / H * 1000), round(x0 / W * 1000), round(y1 / H * 1000), round(x1 / W * 1000)]


PARTS = [  # file, seed, drawing, defect flag, defect box in pixels (y0, x0, y1, x1)
    ("golden_hx310.png", 11, housing, {}, None),
    ("case131_defect.png", 12, housing, {"short": True}, (290, 640, 385, 735)),
    ("golden_bc115.png", 21, cover, {}, None),
    ("case142_defect.png", 22, cover, {"flash": True}, (292, 745, 460, 822)),
    ("golden_gr88.png", 31, gear, {}, None),
    ("case153_defect.png", 32, gear, {"crack": True}, (255, 585, 318, 642)),
]


def parts() -> None:
    for name, seed, draw, kw, box in PARTS:
        _photo(seed, draw, **kw).save(OUT / name, optimize=True)
        print(name, "box_2d", _box(*box) if box else "")


if __name__ == "__main__" and "--parts" in __import__("sys").argv:
    parts()
