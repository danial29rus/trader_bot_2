"""The DKMARKET dragon: an original low-poly dragon head drawn as polygons (no third-party artwork)."""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# Head in profile facing left, in a 1000×1000 box.
HEAD = [
    (70, 560), (150, 505), (260, 470), (360, 445), (430, 400), (475, 345), (540, 305),   # snout → brow → forehead
    (930, 95), (640, 330),                                                                # main horn
    (720, 345), (985, 300), (745, 420),                                                   # second horn
    (800, 470), (960, 500), (830, 560),                                                   # cheek spike
    (900, 600), (985, 660), (880, 700), (960, 780), (860, 810), (920, 900), (830, 920),   # back spikes along the neck
    (860, 990), (650, 990), (640, 850), (590, 760),                                       # curved neck
    (500, 690), (360, 660), (220, 640), (120, 620),                                       # lower jaw
    (180, 590), (330, 585), (110, 575),                                                   # open mouth
]
EYE = [(400, 470), (500, 440), (470, 495)]
NOSTRIL = [(130, 525), (175, 512), (160, 535)]
# Facets: lines from the brow that give the low-poly look.
FACETS = [((475, 345), (560, 560)), ((360, 445), (560, 560)), ((560, 560), (830, 560)), ((590, 760), (860, 810)), ((830, 560), (880, 700)), ((560, 560), (590, 760)),
          ((560, 560), (500, 690)), ((640, 330), (560, 560)), ((720, 345), (800, 470)), ((260, 470), (330, 585))]
TEETH = [[(150, 590), (170, 618), (185, 590)], [(230, 588), (250, 620), (265, 588)], [(200, 578), (215, 555), (232, 580)]]


def _scale(points, box, flip):
    x0, y0, size = box
    return [(x0 + (1000 - x if flip else x) * size / 1000, y0 + y * size / 1000) for x, y in points]


def draw_dragon(canvas: Image.Image, box: tuple[float, float, float], fill, glow_color, eye_color, flip: bool = False, width: int = 4) -> Image.Image:
    """Paint the dragon into `canvas` at (x, y, size) with a neon outline and glow; returns the new image."""
    size = canvas.size
    lines = Image.new("RGB", size)
    draw = ImageDraw.Draw(lines)
    head = _scale(HEAD, box, flip)
    draw.line(head + head[:1], fill=glow_color, width=width * 3, joint="curve")
    glow = lines.filter(ImageFilter.GaussianBlur(width * 6))
    out = ImageChops.add(canvas, glow)

    body = ImageDraw.Draw(out)
    body.polygon(head, fill=fill)
    for a, b in FACETS:
        body.line(_scale([a, b], box, flip), fill=tuple(min(255, c + 22) for c in fill), width=max(1, width // 2))
    for tooth in TEETH:
        body.polygon(_scale(tooth, box, flip), fill=(245, 240, 230))
    body.line(head + head[:1], fill=glow_color, width=width, joint="curve")
    body.polygon(_scale(NOSTRIL, box, flip), fill=glow_color)

    eye_layer = Image.new("RGB", size)
    ImageDraw.Draw(eye_layer).polygon(_scale(EYE, box, flip), fill=eye_color)
    out = ImageChops.add(out, eye_layer.filter(ImageFilter.GaussianBlur(width * 3)))
    ImageDraw.Draw(out).polygon(_scale(EYE, box, flip), fill=eye_color)
    return out
