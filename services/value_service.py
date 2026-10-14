"""Price per unit of in-game currency, so the bot can point out the best-value pack."""
from __future__ import annotations

import re
from dataclasses import dataclass

from database.models import Product

# "60 UC", "86 Diamonds", "78 + 8 Diamonds", "1,800 UC" — a leading amount (with optional bonus) and a unit.
_PACK = re.compile(r"^\s*(\d[\d\s,.]*?)(?:\s*\+\s*(\d[\d\s,.]*?))?\s+([A-Za-zА-Яа-яЁё][\w\s'’-]{0,24}?)\s*(?:\(|$)")
_UNIT_ICONS = {"diamond": "💎", "gem": "💎", "uc": "UC", "robux": "Robux", "coin": "🪙", "crystal": "✨", "primogem": "✨", "token": "🪙"}
MIN_SAVING_PERCENT = 3


@dataclass(frozen=True)
class Pack:
    amount: int
    unit: str

    @property
    def unit_key(self) -> str:
        return self.unit.casefold()

    @property
    def icon(self) -> str:
        key = self.unit_key
        return next((icon for needle, icon in _UNIT_ICONS.items() if needle in key), self.unit)


def _number(raw: str) -> int:
    return int(re.sub(r"[\s,.]", "", raw))


def parse_pack(product: Product) -> Pack | None:
    """Currency amount of a pack; passes, bundles and subscriptions have none."""
    offer = ((product.raw_payload or {}).get("offer") or {}).get("name") or product.name
    match = _PACK.match(str(offer))
    if not match:
        return None
    amount = _number(match.group(1)) + (_number(match.group(2)) if match.group(2) else 0)
    unit = " ".join(match.group(3).split()[:2])
    return Pack(amount, unit) if amount > 0 and unit else None


def unit_price_minor(product: Product, pack: Pack) -> float:
    return product.price_minor / pack.amount


def best_value_ids(products: list[Product]) -> set[int]:
    """The cheapest-per-unit pack for every currency that has at least two packs to compare."""
    groups: dict[str, list[tuple[float, int]]] = {}
    for product in products:
        pack = parse_pack(product)
        if pack:
            groups.setdefault(pack.unit_key, []).append((unit_price_minor(product, pack), product.id))
    return {min(items)[1] for items in groups.values() if len(items) > 1}


def value_line(product: Product, siblings: list[Product]) -> str | None:
    """'0.95 ₽ за 1 💎 · выгоднее самого дорогого на 18%' for the product card."""
    pack = parse_pack(product)
    if not pack:
        return None
    own = unit_price_minor(product, pack)
    line = f"📊 {own / 100:.2f} ₽ за 1 {pack.icon}"
    rates = [unit_price_minor(p, sp) for p in siblings if (sp := parse_pack(p)) and sp.unit_key == pack.unit_key]
    if len(rates) > 1:
        saving = round((1 - own / max(rates)) * 100)
        if product.id in best_value_ids(siblings):
            line += f"\n🔥 Самый выгодный пакет: дешевле за единицу на {saving}%" if saving >= MIN_SAVING_PERCENT else "\n🔥 Самый выгодный пакет"
        elif saving >= MIN_SAVING_PERCENT:
            line += f" · выгоднее самого дорогого на {saving}%"
    return line
