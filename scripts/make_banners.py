"""Render the HonestPlay logo and in-bot banners.

Run with ``python -m scripts.make_banners`` after installing requirements-dev.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "banners"
SIZE = (1280, 720)
BRAND = "HonestPlay"
NAVY, TEAL, CYAN, GOLD, WHITE = (5, 19, 37), (12, 169, 170), (48, 214, 215), (232, 185, 90), (244, 248, 255)

SECTIONS: dict[str, tuple[str, str, tuple[int, int, int]]] = {
    "menu": ("HONESTPLAY", "Цифровые товары без лишнего риска", TEAL),
    "topup": ("ПОПОЛНЕНИЯ", "Валюта на игровой аккаунт", CYAN),
    "roblox": ("ROBLOX", "Robux и подарочные карты", (44, 194, 200)),
    "cards": ("GAME CARDS", "PlayStation · Xbox · Nintendo", (61, 165, 220)),
    "keys": ("GAME KEYS", "Ключи для консолей и ПК", (36, 186, 174)),
    "accounts": ("МОИ ИГРЫ", "Повтор доната в один тап", (77, 203, 165)),
    "catalog": ("КАТАЛОГ", "Все доступные предложения", TEAL),
    "search": ("ПОИСК", "Найдите нужную игру", CYAN),
    "cart": ("КОРЗИНА", "Ваши выбранные товары", (50, 191, 156)),
    "checkout": ("ОФОРМЛЕНИЕ", "Проверьте заказ перед оплатой", GOLD),
    "profile": ("ПРОФИЛЬ", "Личный кабинет", (40, 173, 193)),
    "orders": ("ЗАКАЗЫ", "История покупок", (83, 190, 178)),
    "support": ("ПОДДЕРЖКА", "Мы поможем разобраться", CYAN),
    "docs": ("ДОКУМЕНТЫ", "Прозрачные условия сервиса", (140, 191, 204)),
}

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
TITLE_FONT = FONT_DIR / "Arial Bold.ttf"
TEXT_FONT = FONT_DIR / "Arial.ttf"


def mix(a, b, t: float):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def fitted_font(path: Path, text: str, max_width: int, start: int):
    size = start
    while size > 28:
        result = ImageFont.truetype(str(path), size)
        if result.getlength(text) <= max_width:
            return result
        size -= 3
    return ImageFont.truetype(str(path), size)


def shield_layer(size: tuple[int, int], color: tuple[int, int, int], box: tuple[int, int, int, int]) -> Image.Image:
    layer = Image.new("RGBA", size)
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = box
    middle = (left + right) // 2
    points = [(middle, top), (right - 12, top + 54), (right - 38, bottom - 112), (middle, bottom), (left + 38, bottom - 112), (left + 12, top + 54)]
    draw.polygon(points, fill=(*color, 245), outline=(*GOLD, 235), width=8)
    inner = [(middle, top + 38), (right - 56, top + 78), (right - 77, bottom - 139), (middle, bottom - 37), (left + 77, bottom - 139), (left + 56, top + 78)]
    draw.polygon(inner, fill=(*NAVY, 215))
    return layer


def add_check(image: Image.Image, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = box
    draw.line([(left, (top + bottom) // 2), (left + (right - left) * .31, bottom), (right, top)], fill=(*GOLD, 255), width=max(10, (right - left) // 9), joint="curve")


def logo(size: int = 640) -> Image.Image:
    image = Image.new("RGB", (size, size), NAVY)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        draw.line([(0, y), (size, y)], fill=mix(NAVY, (8, 62, 80), (y / size) ** .7))
    layer = shield_layer((size, size), TEAL, (80, 42, size - 80, size - 38))
    image = Image.alpha_composite(image.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(24)))
    image = Image.alpha_composite(image, layer)
    add_check(image, (size // 4 + 15, size // 2 - 10, size * 3 // 4 - 5, size * 3 // 4 - 15))
    return image.convert("RGB")


def background(color: tuple[int, int, int], seed: int) -> Image.Image:
    image = Image.new("RGB", SIZE, NAVY)
    draw = ImageDraw.Draw(image)
    for y in range(SIZE[1]):
        draw.line([(0, y), SIZE], fill=mix(NAVY, mix((8, 58, 77), color, .28), (y / SIZE[1]) ** 1.35))
    randomizer = random.Random(seed)
    for _ in range(90):
        x, y, radius = randomizer.randrange(SIZE[0]), randomizer.randrange(SIZE[1]), randomizer.choice((1, 1, 2, 2, 3))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=mix(color, WHITE, .45))
    return image


def render(title: str, subtitle: str, color: tuple[int, int, int], number: int) -> Image.Image:
    image = background(color, number).convert("RGBA")
    lights = Image.new("RGBA", SIZE)
    light_draw = ImageDraw.Draw(lights)
    for radius, alpha in ((390, 38), (250, 50), (120, 75)):
        light_draw.ellipse((770 - radius, 355 - radius, 770 + radius, 355 + radius), fill=(*color, alpha))
    image = Image.alpha_composite(image, lights.filter(ImageFilter.GaussianBlur(45)))
    emblem = shield_layer(SIZE, color, (760, 102, 1120, 555))
    image = Image.alpha_composite(image, emblem.filter(ImageFilter.GaussianBlur(28)))
    image = Image.alpha_composite(image, emblem)
    add_check(image, (845, 292, 1037, 432))
    veil = Image.new("L", SIZE)
    veil_draw = ImageDraw.Draw(veil)
    for x in range(SIZE[0]):
        veil_draw.line([(x, 0), (x, SIZE[1])], fill=round(225 * max(0, 1 - x / 700) ** 1.6))
    image = Image.composite(Image.new("RGBA", SIZE, (*NAVY, 255)), image, veil)
    draw = ImageDraw.Draw(image)
    tag_font = ImageFont.truetype(str(TITLE_FONT), 22)
    draw.rounded_rectangle((70, 62, 390, 106), radius=22, outline=(*GOLD, 220), width=2)
    draw.text((92, 72), "HONESTPLAY  ·  GAME STORE", font=tag_font, fill=WHITE)
    title_font = fitted_font(TITLE_FONT, title, 610, 82)
    draw.text((70, 250), title, font=title_font, fill=WHITE)
    draw.text((72, 250 + title_font.size + 25), subtitle, font=fitted_font(TEXT_FONT, subtitle, 580, 31), fill=mix(color, WHITE, .6))
    draw.text((72, 645), "ПРОВЕРЕННЫЙ ВЫБОР", font=tag_font, fill=mix(GOLD, WHITE, .45))
    return image.convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo().save(ROOT / "assets" / "logo.png", optimize=True)
    print("assets/logo.png  (аватарка: @BotFather → /setuserpic)")
    for number, (key, spec) in enumerate(SECTIONS.items(), start=1):
        path = OUT / f"{key}.jpg"
        render(*spec, number).save(path, "JPEG", quality=88, optimize=True, progressive=True)
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
