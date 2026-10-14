"""Render the section banners: a retro arcade scene (neon sun, perspective grid) per section colour.

Run locally after changing the section list:
    python -m scripts.make_banners
Pillow is a dev-only dependency; the bot itself only sends the generated JPEGs.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "banners"
SIZE = (1280, 720)
BRAND = "LVLUP"

# key: (title, subtitle, primary RGB, secondary RGB)
SECTIONS: dict[str, tuple[str, str, tuple[int, int, int], tuple[int, int, int]]] = {
    "menu": ("LVLUP", "Донат в любимые игры", (255, 70, 160), (255, 190, 60)),
    "topup": ("TOP-UP", "Валюта на игровой аккаунт", (255, 60, 120), (255, 170, 40)),
    "roblox": ("ROBLOX", "Robux и карты Roblox", (255, 50, 60), (255, 140, 90)),
    "cards": ("GAME CARDS", "PlayStation · Xbox · Nintendo", (90, 120, 255), (60, 220, 255)),
    "keys": ("GAME KEYS", "Ключи для консолей и ПК", (150, 80, 255), (255, 90, 200)),
    "accounts": ("MY GAMES", "Повтор доната в один тап", (40, 210, 170), (180, 255, 90)),
    "catalog": ("CATALOG", "Все предложения", (120, 100, 255), (255, 120, 200)),
    "search": ("SEARCH", "Поиск по играм", (40, 190, 255), (120, 255, 210)),
    "cart": ("CART", "Ваша корзина", (60, 220, 120), (220, 255, 80)),
    "checkout": ("CHECKOUT", "Оформление заказа", (90, 230, 90), (255, 220, 60)),
    "profile": ("PROFILE", "Личный кабинет", (255, 140, 40), (255, 220, 90)),
    "orders": ("ORDERS", "История заказов", (255, 170, 50), (255, 90, 90)),
    "support": ("SUPPORT", "Поддержка", (60, 200, 230), (150, 120, 255)),
    "docs": ("DOCS", "Документы и правила", (150, 160, 200), (110, 130, 255)),
}

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
TITLE_FONT = FONT_DIR / "Impact.ttf"
TEXT_FONT = FONT_DIR / "Arial Bold.ttf"
HORIZON = 470


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def background(primary) -> Image.Image:
    image = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(image)
    top, mid = (8, 6, 22), mix((20, 8, 40), primary, 0.35)
    for y in range(SIZE[1]):
        t = y / HORIZON if y < HORIZON else 1 - (y - HORIZON) / (SIZE[1] - HORIZON) * 0.8
        draw.line([(0, y), (SIZE[0], y)], fill=mix(top, mid, max(0.0, min(1.0, t)) ** 1.6))
    return image


def sun(primary, secondary) -> Image.Image:
    """Striped retro sun sitting on the horizon, right of centre."""
    layer = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(layer)
    cx, cy, r = 930, HORIZON - 40, 230
    for y in range(cy - r, HORIZON):
        half = math.sqrt(max(0, r * r - (y - cy) ** 2))
        t = (y - (cy - r)) / (2 * r)
        band = y > cy - r * 0.15 and (y // 14) % 3 == 0 and t > 0.45
        if not band:
            draw.line([(cx - half, y), (cx + half, y)], fill=mix(secondary, primary, t))
    return layer


def grid(primary) -> Image.Image:
    layer = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(layer)
    vanish_x = 930
    for i in range(-24, 25):
        x_bottom = vanish_x + i * 120
        draw.line([(vanish_x + i * 6, HORIZON), (x_bottom, SIZE[1])], fill=primary, width=2)
    y, step = HORIZON, 6.0
    while y < SIZE[1]:
        draw.line([(0, round(y)), (SIZE[0], round(y))], fill=primary, width=2)
        step *= 1.35
        y += step
    return layer


def sparkles(seed: int, color) -> Image.Image:
    layer = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(layer)
    rnd = random.Random(seed)
    for _ in range(70):
        x, y, r = rnd.randint(0, SIZE[0]), rnd.randint(0, HORIZON - 30), rnd.choice((1, 1, 2, 2, 3))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=mix(color, (255, 255, 255), 0.6))
    for _ in range(6):
        # Floating "coins": rings that read as in-game currency.
        x, y, r = rnd.randint(560, 1240), rnd.randint(40, 300), rnd.randint(14, 30)
        draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=5)
        draw.ellipse([x - r // 2, y - r // 2, x + r // 2, y + r // 2], outline=color, width=3)
    return layer


def glow(layer: Image.Image, radius: int, strength: float) -> Image.Image:
    return layer.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, round(v * strength)))


def fit_font(path: Path, text: str, max_width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 40:
        font = ImageFont.truetype(str(path), size)
        if font.getlength(text) <= max_width:
            return font
        size -= 4
    return ImageFont.truetype(str(path), size)


def render(title: str, subtitle: str, primary, secondary, number: int) -> Image.Image:
    image = background(primary)
    for layer, blur, strength in ((sun(primary, secondary), 30, 0.9), (grid(primary), 6, 1.4), (sparkles(number, secondary), 4, 1.6)):
        image = ImageChops.add(image, glow(layer, blur, strength))
        image = ImageChops.lighter(image, layer)

    # Dark panel on the left keeps the text readable.
    shade = Image.new("L", SIZE)
    shade_draw = ImageDraw.Draw(shade)
    for x in range(SIZE[0]):
        shade_draw.line([(x, 0), (x, SIZE[1])], fill=round(200 * max(0.0, 1 - x / 720) ** 1.3))
    image = Image.composite(Image.new("RGB", SIZE, (6, 4, 16)), image, shade)

    margin = 72
    title_font = fit_font(TITLE_FONT, title, 620, 150)
    title_y = 210
    text_layer = Image.new("RGB", SIZE)
    ImageDraw.Draw(text_layer).text((margin + 6, title_y + 6), title, font=title_font, fill=primary)
    image = ImageChops.add(image, glow(text_layer, 16, 1.2))
    draw = ImageDraw.Draw(image)
    draw.text((margin + 5, title_y + 5), title, font=title_font, fill=primary)
    draw.text((margin, title_y), title, font=title_font, fill=(255, 250, 240))

    small = ImageFont.truetype(str(TEXT_FONT), 26)
    sub_font = ImageFont.truetype(str(TEXT_FONT), 40)
    tag = f"{BRAND} · GAME TOP-UP"
    draw.rounded_rectangle([margin, 62, margin + 44 + small.getlength(tag), 104], radius=21, outline=secondary, width=3)
    draw.text((margin + 22, 69), tag, font=small, fill=(255, 245, 230))
    draw.text((margin, title_y + title_font.size + 30), subtitle, font=sub_font, fill=secondary)
    draw.text((margin, SIZE[1] - 66), f"LV.{number:02d}", font=small, fill=mix(secondary, (255, 255, 255), 0.4))
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for number, (key, spec) in enumerate(SECTIONS.items(), start=1):
        path = OUT / f"{key}.jpg"
        render(*spec, number=number).save(path, "JPEG", quality=86, optimize=True, progressive=True)
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
