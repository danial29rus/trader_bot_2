"""One-off catalogue snapshot from ReSellCodes: game top-ups, Roblox, gaming gift cards and non-Steam keys.

The snapshot is meant to be run by hand, rarely. It stays under the supplier's
rate limit (paced requests plus a hard request budget), commits after every
supplier category so an interrupted run keeps its progress, and skips
categories that are already imported unless `refresh` is set. Prices are
converted to RUB once (rate × markup, rounded up to whole roubles); after that
the operator owns them in the admin panel, and re-runs keep manually edited
prices (`Product.price_locked`).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Category, Product
from integrations.resellcodes_client import ReSellCodesClient
from services.import_service import get_category, upsert_product

logger = logging.getLogger(__name__)

# Popular games and brands, in the order they appear in the bot. Queries with no match cost one request.
TOPUP_BRANDS = (
    "PUBG Mobile", "Free Fire", "Mobile Legends", "Genshin Impact", "Honkai: Star Rail", "Zenless Zone Zero",
    "Wuthering Waves", "Call of Duty", "Brawl Stars", "Clash of Clans", "Clash Royale", "Hay Day", "Squad Busters",
    "Standoff 2", "Valorant", "League of Legends", "Wild Rift", "Honor of Kings", "Arena Breakout", "Delta Force",
    "Blood Strike", "Farlight 84", "Sausage Man", "Identity V", "Eggy Party", "Stumble Guys", "8 Ball Pool",
    "EA Sports FC", "eFootball", "Marvel Rivals", "Marvel Snap", "Fortnite", "Apex Legends", "Minecraft",
    "Pokemon", "Tower of Fantasy", "Punishing Gray Raven", "Arknights", "Lords Mobile", "Rise of Kingdoms",
    "Whiteout Survival", "Last War", "State of Survival", "Call of Dragons", "Black Desert", "Ragnarok",
    "Dragon Ball", "One Punch Man", "Undawn", "Super Sus", "Love and Deepspace", "Path of Exile", "Warframe",
    "World of Tanks", "War Thunder", "Lineage", "Ace Racer", "Free Fire MAX",
)
ROBLOX_BRANDS = ("Roblox",)
GIFT_CARD_BRANDS = (
    "PlayStation", "Xbox", "Nintendo", "Razer Gold", "Garena", "Riot", "Blizzard", "EA", "Ubisoft",
    "Google Play", "Apple", "Steam", "Twitch", "Discord",
)
# Keys are searched by popular title; Steam keys are left to the first bot.
KEY_GAMES = (
    "Minecraft", "Grand Theft Auto V", "Red Dead Redemption 2", "Forza Horizon 5", "EA Sports FC", "Call of Duty",
    "Diablo IV", "Overwatch", "Elden Ring", "Hogwarts Legacy", "Cyberpunk 2077", "The Sims 4", "Battlefield",
    "Assassin's Creed", "Far Cry", "Mortal Kombat", "Tekken 8", "Spider-Man", "God of War", "Starfield",
    "Fortnite", "Rainbow Six", "Need for Speed", "It Takes Two", "Hollow Knight",
)
KEYS_PER_TITLE = 4
SECTIONS = ("topups", "roblox", "giftcards", "keys")
PLATFORMS = (
    ("xbox", "🟩 Xbox"), ("playstation", "🟦 PlayStation"), ("psn", "🟦 PlayStation"), ("nintendo", "🟥 Nintendo"),
    ("epic", "⬛ Epic Games"), ("battle", "🔷 Battle.net"), ("ea app", "🟧 EA app"), ("origin", "🟧 EA app"),
    ("ubisoft", "🟪 Ubisoft Connect"), ("uplay", "🟪 Ubisoft Connect"), ("rockstar", "🟨 Rockstar"),
    ("gog", "🟫 GOG"), ("microsoft", "🪟 Microsoft Store"), ("windows", "🪟 Microsoft Store"),
)
SUPPLIER_ORIGIN = "https://resell.codes"
# Regions most buyers need come first; the rest follow alphabetically.
POPULAR_REGIONS = ("GLOBAL", "RU", "CIS", "KZ", "TR", "US", "GB", "UK", "EU", "DE", "PL", "UA", "AE", "IN", "BR")
VARIANTS = {"auto": "авто", "fast": "быстро", "manual": "вручную", "reserve": "резерв"}


class BudgetExhausted(RuntimeError):
    pass


class PacedClient(ReSellCodesClient):
    """ReSellCodes client that spaces requests out and refuses to exceed a request budget."""

    def __init__(self, api_key: str, budget: int, per_minute: int):
        super().__init__(api_key)
        self.budget, self.used = budget, 0
        self.interval = 60 / max(per_minute, 1)
        self._last = 0.0
        self.remaining_day: str | None = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(5):
            if self.used >= self.budget:
                raise BudgetExhausted(f"request budget of {self.budget} is used up")
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            self.used += 1
            response = await self.client.get(path, params=params)
            self.remaining_day = response.headers.get("X-RateLimit-Remaining-Day", self.remaining_day)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            await asyncio.sleep(min(int(response.headers.get("Retry-After", "5")), 120) + attempt)
        raise RuntimeError("ReSellCodes rate limit retry limit exceeded")


@dataclass
class Pricing:
    rub_per_usd: Decimal
    markup_percent: Decimal

    def rub_minor(self, price_usd: Any) -> int:
        rub = Decimal(str(price_usd)) * self.rub_per_usd * (1 + self.markup_percent / 100)
        return math.ceil(rub) * 100


@dataclass
class SnapshotResult:
    counters: dict[str, int] = field(default_factory=lambda: {"imported": 0, "skipped_existing": 0, "skipped": 0, "errors": 0})
    warnings: list[str] = field(default_factory=list)
    requests: int = 0
    stopped_by_budget: bool = False


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "offers", "packages", "plans", "items", "prices"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def image_url(path: str | None) -> str | None:
    """Supplier images come as site-relative paths; Telegram needs an absolute URL."""
    if not path:
        return None
    return path if path.startswith(("http://", "https://")) else SUPPLIER_ORIGIN + "/" + path.lstrip("/")


def region_order(region: str | None) -> int:
    code = (region or "GLOBAL").upper()
    return POPULAR_REGIONS.index(code) if code in POPULAR_REGIONS else 1000


def product_name(brand: str, offer: str) -> str:
    return offer if brand.casefold() in offer.casefold() else f"{brand} {offer}"


def region_label(region: str | None, variant: str | None = None) -> str:
    code = (region or "GLOBAL").upper()
    code = "GB" if code == "UK" else code
    variant = VARIANTS.get((variant or "").casefold(), variant)
    if code in {"GLOBAL", "WW", "WORLDWIDE"}:
        label = "🌍 Global"
    elif code == "CIS":
        label = "🌐 СНГ"
    elif re.fullmatch(r"[A-Z]{2}", code):
        label = "".join(chr(0x1F1E6 + ord(c) - 65) for c in code) + f" {code}"
    else:
        label = code
    return f"{label} · {variant}" if variant else label


def _matches(query: str, brand: str) -> bool:
    return query.casefold() in brand.casefold()


async def _already_imported(session: AsyncSession, product_type: str, prefix: str) -> bool:
    # supplier_price_usd is only set by snapshot-era imports, so legacy rows are re-imported once.
    stmt = select(Product.id).where(Product.product_type == product_type, Product.external_id.like(f"{prefix}%"), Product.supplier_price_usd.is_not(None))
    found = await session.scalar(stmt.limit(1))
    return found is not None


async def _brand_snapshot(
    api: PacedClient, session: AsyncSession, pricing: Pricing, result: SnapshotResult, refresh: bool, *,
    brands: tuple[str, ...], brands_path: str, offers_path: str, offers_key: str, offer_id_key: str,
    kind: str, product_type: str, activation: str, title_suffix: str = "", exclude: tuple[str, ...] = (), hide_legacy: bool = True,
) -> None:
    seen: set[str] = set()
    for order, query in enumerate(brands):
        try:
            found = _items(await api.get(brands_path, {"q": query}))
        except BudgetExhausted:
            raise
        except Exception as exc:
            result.counters["errors"] += 1
            result.warnings.append(f"{kind} brand search {query!r}: {exc}")
            continue
        for brand in found:
            name = str(brand.get("brand") or "").strip()
            subcategories = [s for s in brand.get("subcategories", []) if s.get("category_id")]
            if not name or name.casefold() in seen or not _matches(query, name) or not subcategories:
                continue
            if any(_matches(x, name) for x in exclude):
                continue
            seen.add(name.casefold())
            image = image_url(next((s.get("image") for s in subcategories if s.get("image")), None))
            parent = await get_category(session, f"{name}{title_suffix}", kind, f"brand-{name}{title_suffix}", image=image, sort_order=order)
            for sub in subcategories:
                supplier_id = str(sub["category_id"])
                if not refresh and await _already_imported(session, product_type, f"{supplier_id}:"):
                    result.counters["skipped_existing"] += 1
                    continue
                try:
                    details = await api.get(offers_path.format(id=supplier_id))
                except BudgetExhausted:
                    raise
                except Exception as exc:
                    result.counters["errors"] += 1
                    result.warnings.append(f"{kind} {supplier_id}: {exc}")
                    continue
                offers = [o for o in details.get(offers_key, []) if o.get("price_usd") is not None and o.get("stock") != 0]
                if not offers:
                    result.counters["skipped"] += 1
                    continue
                single = len(subcategories) == 1
                sub_image = image_url(sub.get("image") or details.get("image")) or image
                target = parent if single else await get_category(
                    session, region_label(sub.get("region"), sub.get("variant") or None), kind, supplier_id, parent.id,
                    image=sub_image, sort_order=region_order(sub.get("region")),
                )
                fields = details.get("fields") or []
                note = str(details.get("note") or "").strip() or None
                for offer in offers:
                    ok = await upsert_product(
                        session, target, product_type, f"{supplier_id}:{offer[offer_id_key]}", product_name(name, str(offer.get("name") or name)),
                        {"brand": name, "subcategory": sub, "offer": offer, "fields": fields, "note": note},
                        price_usd=offer["price_usd"], price=(pricing.rub_minor(offer["price_usd"]), "RUB"),
                        image=sub_image, region=(sub.get("region") or "GLOBAL"), platform=name,
                        activation=activation, stock=offer.get("stock"),
                    )
                    result.counters["imported" if ok else "skipped"] += 1
                await session.commit()
    if hide_legacy:
        await _hide_legacy_categories(session, kind)


async def _hide_legacy_categories(session: AsyncSession, kind: str) -> None:
    """Older imports put each supplier category at the top level; hide those not adopted into a brand."""
    legacy = (await session.scalars(select(Category).where(
        Category.type == kind, Category.parent_id.is_(None), Category.active, Category.slug.not_like(f"{kind}-brand-%"),
    ))).all()
    for category in legacy:
        category.active = False
        await session.execute(update(Product).where(Product.category_id == category.id, Product.supplier_price_usd.is_(None)).values(active=False))
    await session.commit()


def platform_label(platform: str | None) -> str | None:
    """Readable store name, or None for Steam (sold by the first bot) and unknown blanks."""
    raw = (platform or "").strip()
    if not raw or "steam" in raw.casefold():
        return None
    return next((label for needle, label in PLATFORMS if needle in raw.casefold()), raw)


async def snapshot_keys(api: PacedClient, session: AsyncSession, pricing: Pricing, result: SnapshotResult, refresh: bool) -> None:
    platforms: dict[str, Category] = {}
    for title in KEY_GAMES:
        try:
            games = _items(await api.get("/game-keys", {"q": title, "limit": 20}))
        except BudgetExhausted:
            raise
        except Exception as exc:
            result.counters["errors"] += 1
            result.warnings.append(f"keys search {title!r}: {exc}")
            continue
        picked = [g for g in games if g.get("game_id") and platform_label(g.get("platform")) and _matches(title, str(g.get("name") or ""))][:KEYS_PER_TITLE]
        for game in picked:
            game_id = str(game["game_id"])
            if not refresh and await _already_imported(session, "game_key", f"{game_id}:"):
                result.counters["skipped_existing"] += 1
                continue
            try:
                details = await api.get(f"/game-keys/{game_id}/keys")
            except BudgetExhausted:
                raise
            except Exception as exc:
                result.counters["errors"] += 1
                result.warnings.append(f"keys {game_id}: {exc}")
                continue
            keys = [k for k in details.get("keys", []) if k.get("price_usd") is not None and k.get("stock") != 0]
            if not keys:
                result.counters["skipped"] += 1
                continue
            label = platform_label(game.get("platform"))
            if label not in platforms:
                order = next((i for i, (_, known) in enumerate(PLATFORMS) if known == label), 100)
                platforms[label] = await get_category(session, label, "game_key", f"platform-{label}", sort_order=order)
            name = str(game.get("name") or title)
            restriction = game.get("region_restriction") or details.get("region_restriction")
            description = f"Платформа: {game.get('platform')}. Регион ключа: {game.get('region') or '—'}."
            if restriction:
                description += f"\nОграничения региона: {restriction}"
            for key in keys:
                key_name = str(key.get("name") or "")
                full = name if not key_name or key_name.casefold() in name.casefold() else product_name(name, key_name)
                ok = await upsert_product(
                    session, platforms[label], "game_key", f"{game_id}:{key['key_id']}", full, {"game": game, "key": key},
                    price_usd=key["price_usd"], price=(pricing.rub_minor(key["price_usd"]), "RUB"), image=image_url(game.get("image")),
                    platform=str(game.get("platform") or ""), region=game.get("region"), activation="Ключ активации",
                    stock=key.get("stock"), description=description,
                )
                result.counters["imported" if ok else "skipped"] += 1
            await session.commit()


async def run_snapshot(
    session: AsyncSession, api_key: str, pricing: Pricing, *, sections: tuple[str, ...] = SECTIONS,
    budget: int = 1500, per_minute: int = 25, refresh: bool = False,
) -> SnapshotResult:
    result = SnapshotResult()
    async with PacedClient(api_key, budget, per_minute) as api:
        try:
            topups = dict(brands_path="/top-ups/brands", offers_path="/top-ups/categories/{id}/offers", offers_key="offers",
                          offer_id_key="offer_id", product_type="game_topup", activation="Пополнение на аккаунт")
            cards = dict(brands_path="/gift-cards/brands", offers_path="/gift-cards/categories/{id}/cards", offers_key="offers",
                         offer_id_key="card_id", product_type="gift_card", activation="code")
            if "topups" in sections:
                await _brand_snapshot(api, session, pricing, result, refresh, brands=TOPUP_BRANDS, kind="game_topup", exclude=ROBLOX_BRANDS, **topups)
            if "roblox" in sections:
                await _brand_snapshot(api, session, pricing, result, refresh, brands=ROBLOX_BRANDS, kind="roblox", title_suffix=" · Robux", hide_legacy=False, **topups)
                await _brand_snapshot(api, session, pricing, result, refresh, brands=ROBLOX_BRANDS, kind="roblox", title_suffix=" · подарочные карты", hide_legacy=False, **cards)
            if "giftcards" in sections:
                await _brand_snapshot(api, session, pricing, result, refresh, brands=GIFT_CARD_BRANDS, kind="gift_card", exclude=ROBLOX_BRANDS, **cards)
            if "keys" in sections:
                await snapshot_keys(api, session, pricing, result, refresh)
        except BudgetExhausted as exc:
            await session.commit()
            result.stopped_by_budget = True
            result.warnings.append(f"{exc}; run again later to continue — imported categories are skipped")
        finally:
            result.requests = api.used
            if api.remaining_day is not None:
                result.warnings.append(f"supplier daily requests remaining: {api.remaining_day}")
    return result


async def probe(api_key: str) -> dict[str, Any]:
    """A handful of read-only calls showing the real response shapes for this account."""
    samples: dict[str, Any] = {}
    async with PacedClient(api_key, budget=12, per_minute=20) as api:
        async def take(label: str, path: str, params: dict[str, Any] | None = None) -> Any:
            try:
                samples[label] = await api.get(path, params)
            except Exception as exc:
                samples[label] = {"error": str(exc)}
            return samples[label]

        me = await take("me", "/me")
        if isinstance(me, dict):
            me.pop("email", None)
        topups = await take("topup_brands_pubg", "/top-ups/brands", {"q": "PUBG"})
        await take("topup_brands_roblox", "/top-ups/brands", {"q": "Roblox"})
        cards = await take("giftcard_brands_razer", "/gift-cards/brands", {"q": "Razer Gold"})
        keys = await take("game_keys_minecraft", "/game-keys", {"q": "Minecraft", "limit": 20})
        for label, payload, path in (("topup_offers_sample", topups, "/top-ups/categories/{id}/offers"), ("giftcard_cards_sample", cards, "/gift-cards/categories/{id}/cards")):
            sub = next((s for b in _items(payload) for s in b.get("subcategories", []) if s.get("category_id")), None)
            if sub:
                await take(label, path.format(id=sub["category_id"]))
        game = next((g for g in _items(keys) if g.get("game_id") and platform_label(g.get("platform"))), None)
        if game:
            await take("game_key_offers_sample", f"/game-keys/{game['game_id']}/keys")
        samples["_requests_used"] = api.used
        samples["_daily_remaining"] = api.remaining_day
    return samples


async def count_by_section(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(Product.product_type, func.count()).where(Product.active).group_by(Product.product_type))
    return {kind: count for kind, count in rows.all()}

