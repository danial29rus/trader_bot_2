from __future__ import annotations

import math
import re
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from bot import panel
from bot.keyboards.inline import kb, main_menu, nav
from bot.states import CartInput, Checkout, Search
from config import get_settings
from database.models import Category, GameAccount, Order, Product, PromoCode, User
from database.session import SessionLocal
from services import cart_service, catalog_service, order_service
from services.legal import payment_and_refunds, prices_and_tariffs, privacy_policy, service_rules, terms_of_service

router = Router()
settings = get_settings()
PAGE_SIZE = 6
BANNER_BY_TYPE = {"game_topup": "topup", "roblox": "roblox", "gift_card": "cards", "game_key": "keys"}
PRODUCT_KINDS = {"gift_card": "💳 Игровая карта (код)", "game_key": "🔑 Ключ активации", "game_topup": "⚡ Донат на аккаунт"}


async def user_for(session: AsyncSession, message: Message | CallbackQuery) -> User:
    tg = message.from_user
    assert tg
    user = await session.scalar(select(User).where(User.telegram_id == tg.id))
    if not user:
        user = User(telegram_id=tg.id, username=tg.username, first_name=tg.first_name)
        session.add(user)
        await session.flush()
    else:
        user.username, user.first_name = tg.username, tg.first_name
    return user


async def render(callback: CallbackQuery, text: str, markup=None, banner: str | None = None) -> None:
    try:
        await callback.answer()
    except Exception:
        # Already answered with a toast/alert by the handler.
        pass
    await panel.show(callback.bot, callback.message.chat.id, text, markup, banner, message=panel.clicked(callback))


async def reply(message: Message, text: str, markup=None, banner: str | None = None) -> None:
    """Answer a typed message inside the panel and remove the typed message itself."""
    await panel.consume(message)
    await panel.show(message.bot, message.chat.id, text, markup, banner)


def plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    word = many if 11 <= tail <= 14 else one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many
    return f"{n} {word}"


def offers_count(n: int) -> str:
    return plural(n, "предложение", "предложения", "предложений")


def banner_for(kind: str | None) -> str:
    return BANNER_BY_TYPE.get(kind or "", "catalog")


def status_text(status: str) -> str:
    return {"pending_payment": "ожидает оплаты", "paid_test": "оплачен", "cancelled": "отменён"}.get(status, status)


def product_text(product: Product) -> str:
    kind = PRODUCT_KINDS.get(product.product_type, product.product_type)
    fields = [f"<b>{escape(product.name)}</b>", kind, ""]
    for label, value in (("Платформа", product.platform), ("Регион", product.region), ("Способ выдачи", product.activation_type)):
        if value:
            fields.append(f"{label}: {escape(value)}")
    if product.description:
        fields.append("\n" + escape(product.description[:500]))
    needed = [f["label"] for f in input_fields(product)]
    if needed:
        fields.append("\n📝 Понадобится: " + escape(", ".join(needed)))
    fields.append(f"\n💰 Цена: <b>{catalog_service.money(product.price_minor, product.currency)}</b>")
    fields.append("✅ В наличии")
    return "\n".join(fields)


def input_fields(product: Product) -> list[dict[str, str]]:
    """Recipient data the supplier needs for this product (player ID, Telegram username…)."""
    raw = (product.raw_payload or {}).get("fields") or []
    return [{"key": str(f.get("key")), "label": str(f.get("label") or f.get("key"))} for f in raw if isinstance(f, dict) and f.get("key")]


def offer_label(product: Product) -> str:
    name = product.name.strip()
    price = catalog_service.money(product.price_minor, product.currency)
    variant = f" [{product.region}]" if product.region else ""
    available_for_name = max(12, 64 - len(variant) - len(price) - 3)
    short_name = name if len(name) <= available_for_name else name[:available_for_name - 1].rstrip() + "…"
    return f"{short_name}{variant} · {price}"[:64]


def documents_keyboard():
    return kb(
        [("🔒 Конфиденциальность", "doc:privacy")],
        [("📜 Пользовательское соглашение", "doc:terms")],
        [("💳 Цены и тарифы", "doc:prices")],
        [("↩️ Оплата и отмена", "doc:payments")],
        [("🛡 Правила сервиса", "doc:rules")],
        nav(),
    )


async def main_text() -> str:
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(Product).where(Product.active)) or 0
    return (
        f"<b>{escape(settings.store_name)}</b> — донат в любимые игры\n\n"
        "⚡ Валюта в PUBG, Free Fire, Genshin, MLBB и ещё десятках игр\n"
        "🟥 Robux и карты Roblox\n"
        "💳 Карты PlayStation, Xbox, Nintendo, Razer Gold\n"
        "🔑 Ключи игр для консолей и ПК\n\n"
        "🎮 Бот запоминает ваш игровой ID — следующий донат в один тап.\n\n"
        f"<i>В каталоге {offers_count(count)}.</i> Выберите раздел 👇"
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    async with SessionLocal() as session, session.begin():
        await user_for(session, message)
    await panel.show(message.bot, message.chat.id, await main_text(), main_menu(), "menu", new=True)


@router.callback_query(F.data == "menu")
async def menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, await main_text(), main_menu(), "menu")


SECTION_TITLES = {
    "game_topup": ("⚡ <b>Донат в игры</b>", "Валюта и пропуски зачисляются прямо на игровой аккаунт по ID. Выберите игру:"),
    "roblox": ("🟥 <b>Roblox</b>", "Robux на аккаунт или подарочная карта Roblox:"),
    "gift_card": ("💳 <b>Игровые карты</b>", "Карты пополнения кошельков и магазинов: PlayStation, Xbox, Nintendo, Razer Gold и другие. Выберите бренд:"),
    "game_key": ("🔑 <b>Ключи игр</b>", "Ключи для Xbox, PlayStation, Epic Games, Battle.net и других магазинов. Выберите платформу:"),
}
GRID_PAGE = 10


def category_grid(rows: list[Category], page: int, page_data: str, back: str) -> tuple[object, int, int]:
    """Two buttons per row with pagination; returns (markup, page, pages)."""
    pages = max(1, math.ceil(len(rows) / GRID_PAGE))
    page = min(max(1, page), pages)
    chunk = rows[(page - 1) * GRID_PAGE: page * GRID_PAGE]
    buttons = [[(x.name[:40], f"cat:{x.id}") for x in chunk[i:i + 2]] for i in range(0, len(chunk), 2)]
    if pages > 1:
        controls = []
        if page > 1: controls.append(("⬅️", f"{page_data}:{page - 1}"))
        controls.append((f"{page}/{pages}", "noop"))
        if page < pages: controls.append(("➡️", f"{page_data}:{page + 1}"))
        buttons.append(controls)
    buttons += [[("🔎 Поиск", "search")], nav(back)]
    return kb(*buttons), page, pages


async def active_children(session: AsyncSession, parent_id: int | None, kind: str | None = None) -> list[Category]:
    stmt = select(Category).where(Category.active, Category.parent_id == parent_id if parent_id else Category.parent_id.is_(None))
    if kind:
        stmt = stmt.where(Category.type == kind)
    return list((await session.scalars(stmt.order_by(Category.sort_order, Category.name))).all())


@router.callback_query(F.data.startswith("cat_type:"))
async def categories_by_type(callback: CallbackQuery) -> None:
    _, kind, *rest = callback.data.split(":")
    page = int(rest[0]) if rest else 1
    async with SessionLocal() as session:
        rows = await active_children(session, None, kind)
    if not rows:
        await render(callback, "<b>Раздел скоро откроется</b>\nМы ещё наполняем его товарами. Загляните в другие разделы или воспользуйтесь поиском.", kb([("🔎 Поиск", "search")], nav()), banner_for(kind))
        return
    if len(rows) == 1:
        await show_category(callback, rows[0].id, 1)
        return
    title, hint = SECTION_TITLES.get(kind, ("<b>Каталог</b>", "Выберите категорию:"))
    markup, page, pages = category_grid(rows, page, f"cat_type:{kind}", "menu")
    counter = f"{len(rows)} · страница {page} из {pages}" if pages > 1 else f"{len(rows)}"
    label = "Платформ" if kind == "game_key" else "Игр и брендов"
    await render(callback, f"{title}\n{hint}\n\n<i>{label}: {counter}</i>", markup, banner_for(kind))


async def category_back(category: Category | None) -> str:
    if not category:
        return "menu"
    if category.parent_id:
        return f"cat:{category.parent_id}"
    return f"cat_type:{category.type}"


async def show_category(callback: CallbackQuery, category_id: int, page: int) -> None:
    async with SessionLocal() as session:
        parent = await session.get(Category, category_id)
        child = await active_children(session, category_id)
        siblings = await active_children(session, None, parent.type) if parent and not parent.parent_id else []
    if not child:
        await render_list(callback, category_id, 1)
        return
    # A section with a single top-level category goes straight back to the menu.
    back = "menu" if parent and not parent.parent_id and len(siblings) <= 1 else await category_back(parent)
    markup, page, pages = category_grid(child, page, f"catp:{category_id}", back)
    kind = parent.type if parent else None
    hint = {
        "gift_card": "Выберите регион карты — он должен совпадать с регионом аккаунта:",
        "game_topup": "Выберите регион и способ пополнения.\n<i>Авто — моментально, вручную — оператор, дольше.</i>",
        "roblox": "Выберите регион и способ:",
    }.get(kind, "Выберите вариант:")
    header = f"<b>{escape(parent.name) if parent else 'Каталог'}</b>\n{hint}"
    await render(callback, header, markup, banner_for(kind))


@router.callback_query(F.data.startswith("cat:"))
async def category(callback: CallbackQuery) -> None:
    await show_category(callback, int(callback.data.split(":")[1]), 1)


@router.callback_query(F.data.startswith("catp:"))
async def category_page(callback: CallbackQuery) -> None:
    _, category_id, page = callback.data.split(":")
    await show_category(callback, int(category_id), int(page))


async def render_list(callback: CallbackQuery, category_id: int, page: int) -> None:
    async with SessionLocal() as session:
        products, total = await catalog_service.product_page(session, category_id, max(1, page), PAGE_SIZE)
        category = await session.get(Category, category_id)
        parent = await session.get(Category, category.parent_id) if category and category.parent_id else None
    banner = banner_for(category.type if category else None)
    back = await category_back(category)
    if not products:
        await render(callback, "<b>Здесь пока пусто</b>\nТовары в этой категории скоро появятся.", kb([("🔎 Поиск", "search")], nav(back)), banner)
        return
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, pages)
    buttons = [[(offer_label(p), f"prod:{p.id}:{category_id}:{page}")] for p in products]
    controls = []
    if page > 1: controls.append(("⬅️", f"list:{category_id}:{page - 1}"))
    controls.append((f"{page}/{pages}", "noop"))
    if page < pages: controls.append(("➡️", f"list:{category_id}:{page + 1}"))
    buttons += [controls, nav(back)]
    title = escape(category.name) if category else "Каталог"
    if parent:
        title = f"{escape(parent.name)} · {title}"
    await render(callback, f"<b>{title}</b>\n{offers_count(total)} · страница {page} из {pages}\n<i>Сначала самые доступные</i>", kb(*buttons), banner)


@router.callback_query(F.data.startswith("list:"))
async def list_page(callback: CallbackQuery) -> None:
    _, category_id, page = callback.data.split(":")
    await render_list(callback, int(category_id), int(page))


def product_keyboard(item: Product, back: str):
    return kb([("🛒 В корзину", f"add:{item.id}"), ("🧺 Корзина", "cart")], [("⬅️ К списку", back), ("🏠 Меню", "menu")])


async def show_product(callback: CallbackQuery, item: Product, back: str, note: str = "") -> None:
    try:
        await callback.answer()
    except Exception:
        pass
    await panel.show(callback.bot, callback.message.chat.id, product_text(item) + note, product_keyboard(item, back),
                     banner_for(item.product_type), message=panel.clicked(callback), image=item.image_url)


@router.callback_query(F.data.startswith("prod:"))
async def product(callback: CallbackQuery) -> None:
    _, product_id, category_id, page = callback.data.split(":")
    async with SessionLocal() as session:
        item = await session.get(Product, int(product_id))
    if not item or not item.active:
        await render(callback, "<b>Товар больше недоступен</b>\nВозможно, он закончился у поставщика.", kb(nav(f"list:{category_id}:{page}")), "catalog")
        return
    await show_product(callback, item, "menu" if category_id == "0" else f"list:{category_id}:{page}")


@router.callback_query(F.data == "search")
async def search_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Search.waiting_query)
    await render(callback, "<b>Поиск по каталогу</b>\nНапишите название игры или сервиса одним сообщением.\n\nНапример: <code>Roblox</code>, <code>Counter-Strike</code>, <code>PlayStation</code>", kb(nav()), "search")


@router.message(Search.waiting_query)
async def search_receive(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()[:80]
    retry = kb([("🔎 Искать снова", "search")], nav())
    if not query:
        await reply(message, "<b>Поиск по каталогу</b>\nОтправьте название текстом.", kb(nav()), "search")
        return
    await state.clear()
    async with SessionLocal() as session:
        rows, total = await catalog_service.search_products(session, query, 1, PAGE_SIZE)
    if not rows:
        await reply(message, f"<b>По запросу «{escape(query)}» ничего не нашлось</b>\nПроверьте написание или попробуйте короче — например, только название игры.", retry, "search")
        return
    shown = f"показаны первые {len(rows)}" if total > len(rows) else "показаны все"
    await reply(
        message,
        f"<b>Поиск: «{escape(query)}»</b>\nНайдено {total} · {shown}\n<i>Сначала самые доступные</i>",
        kb(*[[(offer_label(p), f"prod:{p.id}:0:1")] for p in rows], [("🔎 Искать снова", "search")], nav()),
        "search",
    )


TELEGRAM_USERNAME = re.compile(r"@?([A-Za-z][A-Za-z0-9_]{3,31})")


def field_prompt(item: Product, fields: list[dict[str, str]], index: int, error: str = "") -> str:
    field = fields[index]
    step = f"Шаг {index + 1} из {len(fields)} · " if len(fields) > 1 else ""
    hint = "\nНапример: <code>@durov</code>" if field["key"] == "telegram_username" else ""
    text = f"<b>{escape(item.name)}</b>\n{step}данные получателя\n\nОтправьте одним сообщением: <b>{escape(field['label'])}</b>{hint}"
    text += "\n\n<i>Пароли и коды входа не нужны — никогда не отправляйте их.</i>"
    return text + (f"\n\n⚠️ {error}" if error else "")


def validate_field(key: str, value: str) -> tuple[str | None, str]:
    """Returns (normalized value, error)."""
    if key == "telegram_username":
        match = TELEGRAM_USERNAME.fullmatch(value)
        return (f"@{match.group(1)}", "") if match else (None, "Username: латиница, цифры и «_», от 5 символов.")
    if not value or "\n" in value or len(value) > 64:
        return None, "Отправьте значение одной строкой, до 64 символов."
    return value, ""


async def add_to_cart(user_tg: Message | CallbackQuery, product_id: int, configuration: dict | None) -> str:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, user_tg)
        await cart_service.add_item(session, user.id, product_id, configuration)
    return "Добавлено в корзину"


async def root_category_id(session: AsyncSession, category_id: int | None) -> int | None:
    """The game (top-level brand) a product belongs to, whatever region/variant it is listed under."""
    seen: set[int] = set()
    while category_id and category_id not in seen:
        seen.add(category_id)
        category = await session.get(Category, category_id)
        if not category or not category.parent_id:
            return category_id
        category_id = category.parent_id
    return category_id


async def saved_account(session: AsyncSession, user_id: int, item: Product) -> GameAccount | None:
    """A saved account is reused only when it has exactly the fields this product asks for."""
    root = await root_category_id(session, item.category_id)
    account = await session.scalar(select(GameAccount).where(GameAccount.user_id == user_id, GameAccount.category_id == root)) if root else None
    keys = sorted(f["key"] for f in input_fields(item))
    if account and sorted(f["key"] for f in account.fields) == keys:
        return account
    return None


async def remember_account(user_tg: Message | CallbackQuery, item: Product, values: list[dict]) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, user_tg)
        root = await root_category_id(session, item.category_id)
        if not root:
            return
        statement = insert(GameAccount).values(user_id=user.id, category_id=root, fields=values, last_product_id=item.id)
        await session.execute(statement.on_conflict_do_update(
            constraint="uq_game_account", set_={"fields": values, "last_product_id": item.id, "updated_at": func.now()},
        ))


def fields_summary(values: list[dict]) -> str:
    return "\n".join(f"{escape(v['label'])}: <b>{escape(v['value'])}</b>" for v in values)


def added_screen(item: Product, values: list[dict]) -> tuple[str, object]:
    text = f"✅ <b>Добавлено в корзину</b>\n{escape(item.name)} · {catalog_service.money(item.price_minor, item.currency)}"
    if values:
        text += f"\n\n{fields_summary(values)}\n<i>Аккаунт сохранён в «🎮 Мои игры» — в следующий раз донат в один тап.</i>"
    markup = kb([("🧺 Перейти в корзину", "cart")], [("🛍 Ещё пакет", f"list:{item.category_id}:1"), ("🏠 Меню", "menu")])
    return text, markup


@router.callback_query(F.data.startswith("add:"))
async def add(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session, session.begin():
        item = await session.get(Product, product_id)
        fields = input_fields(item) if item else []
        user = await user_for(session, callback)
        account = await saved_account(session, user.id, item) if fields else None
    if account:
        await render(
            callback,
            f"<b>{escape(item.name)}</b>\n\nПополнить сохранённый аккаунт?\n{fields_summary(account.fields)}",
            kb([("✅ Да, на этот аккаунт", f"addsaved:{product_id}")], [("✏️ Другой аккаунт", f"addnew:{product_id}"), ("✖️ Отмена", f"addcancel:{product_id}")]),
            banner_for(item.product_type),
        )
        return
    if fields:
        await ask_fields(callback, state, item, fields)
        return
    try:
        await callback.answer(await add_to_cart(callback, product_id, None))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)


async def ask_fields(callback: CallbackQuery, state: FSMContext, item: Product, fields: list[dict[str, str]]) -> None:
    await state.set_state(CartInput.waiting_value)
    await state.update_data(add_product=item.id, add_values=[])
    await render(callback, field_prompt(item, fields, 0), kb([("✖️ Отмена", f"addcancel:{item.id}")]), banner_for(item.product_type))


@router.callback_query(F.data.startswith("addnew:"))
async def add_new_account(callback: CallbackQuery, state: FSMContext) -> None:
    async with SessionLocal() as session:
        item = await session.get(Product, int(callback.data.split(":")[1]))
    if not item:
        await render(callback, "<b>Товар больше недоступен</b>", kb(nav()), "catalog")
        return
    await ask_fields(callback, state, item, input_fields(item))


async def add_with_account(callback: CallbackQuery, item: Product, values: list[dict]) -> None:
    try:
        await add_to_cart(callback, item.id, {"fields": values})
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await remember_account(callback, item, values)
    text, markup = added_screen(item, values)
    await render(callback, text, markup, "cart")


@router.callback_query(F.data.startswith("addsaved:"))
async def add_saved(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        item = await session.get(Product, int(callback.data.split(":")[1]))
        user = await user_for(session, callback)
        account = await saved_account(session, user.id, item) if item else None
    if not item or not account:
        await render(callback, "<b>Аккаунт не найден</b>\nВведите данные заново.", kb(nav()), "catalog")
        return
    await add_with_account(callback, item, account.fields)


@router.callback_query(F.data.startswith("addcancel:"))
async def add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        item = await session.get(Product, int(callback.data.split(":")[1]))
    if not item:
        await render(callback, await main_text(), main_menu(), "menu")
        return
    await show_product(callback, item, f"list:{item.category_id}:1")


@router.message(CartInput.waiting_value)
async def add_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with SessionLocal() as session:
        item = await session.get(Product, data.get("add_product"))
    if not item:
        await state.clear()
        await reply(message, "<b>Товар больше недоступен</b>", kb(nav()), "catalog")
        return
    fields, values = input_fields(item), list(data.get("add_values") or [])
    cancel = kb([("✖️ Отмена", f"addcancel:{item.id}")])
    field = fields[len(values)]
    value, error = validate_field(field["key"], (message.text or "").strip())
    if error:
        await reply(message, field_prompt(item, fields, len(values), error), cancel, banner_for(item.product_type))
        return
    values.append({"key": field["key"], "label": field["label"], "value": value})
    if len(values) < len(fields):
        await state.update_data(add_values=values)
        await reply(message, field_prompt(item, fields, len(values)), cancel, banner_for(item.product_type))
        return
    await state.clear()
    try:
        await add_to_cart(message, item.id, {"fields": values})
    except ValueError as exc:
        await reply(message, f"<b>Не получилось добавить</b>\n{escape(str(exc))}", kb(nav()), banner_for(item.product_type))
        return
    await remember_account(message, item, values)
    text, markup = added_screen(item, values)
    await reply(message, text, markup, "cart")


@router.callback_query(F.data == "accounts")
async def accounts(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        rows = list((await session.scalars(
            select(GameAccount).options(selectinload(GameAccount.category)).where(GameAccount.user_id == user.id).order_by(GameAccount.updated_at.desc())
        )).all())
    if not rows:
        await render(
            callback,
            "🎮 <b>Мои игры</b>\n\nЗдесь появятся аккаунты, на которые вы донатили. Бот запомнит ID, и следующий донат займёт один тап.",
            kb([("⚡ Донат в игры", "cat_type:game_topup")], nav()), "accounts",
        )
        return
    buttons = []
    for account in rows[:20]:
        first = account.fields[0]["value"] if account.fields else ""
        buttons.append([(f"{account.category.name[:28]} · {first[:20]}", f"acc:{account.id}")])
    buttons.append(nav())
    await render(callback, f"🎮 <b>Мои игры</b>\nСохранённых аккаунтов: {len(rows)}\nВыберите, чтобы повторить донат:", kb(*buttons), "accounts")


async def own_account(callback: CallbackQuery, account_id: int) -> GameAccount | None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        return await session.scalar(
            select(GameAccount).options(selectinload(GameAccount.category), selectinload(GameAccount.last_product))
            .where(GameAccount.id == account_id, GameAccount.user_id == user.id)
        )


@router.callback_query(F.data.startswith("acc:"))
async def account_view(callback: CallbackQuery) -> None:
    account = await own_account(callback, int(callback.data.split(":")[1]))
    if not account:
        await render(callback, "<b>Аккаунт не найден</b>", kb(nav("accounts")), "accounts")
        return
    last = account.last_product if account.last_product and account.last_product.active else None
    text = f"🎮 <b>{escape(account.category.name)}</b>\n\n{fields_summary(account.fields)}"
    buttons = []
    if last:
        text += f"\n\nПоследний донат: {escape(last.name)} · {catalog_service.money(last.price_minor, last.currency)}"
        buttons.append([(f"🔁 Повторить · {catalog_service.money(last.price_minor, last.currency)}", f"accrep:{account.id}")])
    buttons += [[("⚡ Выбрать другой пакет", f"cat:{account.category_id}")], [("🗑 Забыть аккаунт", f"accdel:{account.id}")], nav("accounts")]
    await render(callback, text, kb(*buttons), "accounts")


@router.callback_query(F.data.startswith("accrep:"))
async def account_repeat(callback: CallbackQuery) -> None:
    account = await own_account(callback, int(callback.data.split(":")[1]))
    if not account or not account.last_product or not account.last_product.active:
        await callback.answer("Этот пакет больше недоступен — выберите другой", show_alert=True)
        return
    await add_with_account(callback, account.last_product, account.fields)


@router.callback_query(F.data.startswith("accdel:"))
async def account_delete(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        account = await session.scalar(select(GameAccount).where(GameAccount.id == int(callback.data.split(":")[1]), GameAccount.user_id == user.id))
        if account:
            await session.delete(account)
    await callback.answer("Аккаунт удалён")
    await accounts(callback)


async def render_cart(callback: CallbackQuery, user_id: int) -> None:
    async with SessionLocal() as session:
        lines = await cart_service.get_cart(session, user_id)
    if not lines:
        await render(callback, "<b>Корзина пуста</b>\nДобавьте товары из каталога — они появятся здесь.", kb([("🎮 Steam", "cat_type:steam_gift"), ("🎁 Карты", "cat_type:gift_card")], nav()), "cart")
        return
    total = sum(line.product.price_minor * line.quantity for line in lines)
    text = ["<b>Корзина</b>"]
    buttons = []
    for line in lines:
        text.append(f"• {escape(line.product.name)} — {catalog_service.money(line.product.price_minor)} × {line.quantity} = <b>{catalog_service.money(line.product.price_minor * line.quantity)}</b>")
        for value in (line.configuration or {}).get("fields", []):
            text.append(f"   ↳ {escape(value['label'])}: <code>{escape(value['value'])}</code>")
        buttons.append([("➖", f"qty:{line.id}:-1"), (f"{line.quantity} шт.", "noop"), ("➕", f"qty:{line.id}:1"), ("🗑", f"rm:{line.id}")])
    text.append(f"\nИтого: <b>{catalog_service.money(total)}</b>")
    buttons += [[("✅ Оформить заказ", "checkout")], [("🧹 Очистить", "clear"), ("🛍 Продолжить", "menu")], nav()]
    await render(callback, "\n".join(text), kb(*buttons), "cart")


@router.callback_query(F.data == "cart")
async def cart(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        user_id = user.id
    await render_cart(callback, user_id)


@router.callback_query(F.data.startswith("qty:"))
async def quantity(callback: CallbackQuery) -> None:
    _, item_id, delta = callback.data.split(":")
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        ok = await cart_service.change_quantity(session, user.id, int(item_id), int(delta))
        user_id = user.id
    if not ok:
        await callback.answer("Строка уже удалена", show_alert=True)
    await render_cart(callback, user_id)


@router.callback_query(F.data.startswith("rm:"))
async def remove(callback: CallbackQuery) -> None:
    item_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        await cart_service.change_quantity(session, user.id, item_id, -10**9)
        user_id = user.id
    await render_cart(callback, user_id)


@router.callback_query(F.data == "clear")
async def clear(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        await cart_service.clear_cart(session, user.id)
        user_id = user.id
    await render_cart(callback, user_id)


async def checkout_view(user_id: int, promo_code: str | None, use_bonus: bool) -> tuple[str, object]:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        lines = await cart_service.get_cart(session, user_id)
        if not lines:
            return "<b>Корзина пуста</b>\nДобавьте товары из каталога, чтобы оформить заказ.", kb([("🛍 В каталог", "menu")], nav())
        subtotal = sum(line.product.price_minor * line.quantity for line in lines)
        normalized_code = order_service.normalize_promo_code(promo_code)
        promo_discount = 0
        promo_line = "Промокод: <i>не выбран</i>"
        if normalized_code:
            promo = await session.scalar(select(PromoCode).where(PromoCode.code == normalized_code))
            if promo and order_service.is_promo_available(promo):
                promo_discount = subtotal * promo.discount_percent // 100
                promo_line = f"Промокод <b>{escape(promo.code)}</b>: −{catalog_service.money(promo_discount)}"
            else:
                normalized_code = None
                promo_line = "Промокод: <i>недоступен, введите другой</i>"
        after_promo = subtotal - promo_discount
        balance = user.bonus_balance_minor if user else 0
        bonus_used = min(balance, after_promo) if use_bonus else 0
        bonus_line = f"Бонусный баланс: <b>{catalog_service.money(balance)}</b>"
        if bonus_used:
            bonus_line += f"\nК списанию: −{catalog_service.money(bonus_used)}"
        total = after_promo - bonus_used
    text = (
        "<b>Оформление заказа</b>\n\n"
        f"Сумма товаров: <b>{catalog_service.money(subtotal)}</b>\n"
        f"{promo_line}\n{bonus_line}\n"
        f"К оплате: <b>{catalog_service.money(total)}</b>\n\n"
        "<i>В данный момент оплата находится на стадии подключения.</i>"
    )
    buttons = [[("🏷 Ввести промокод", "checkout:promo")]]
    if normalized_code:
        buttons.append([("✖️ Убрать промокод", "checkout:clear_promo")])
    bonus_button = "✅ Списать бонусы" if use_bonus else "🎁 Списать бонусы"
    buttons.append([(bonus_button, "checkout:bonus")])
    buttons += [[("📄 Условия оплаты", "doc:payments"), ("💬 Поддержка", "support")], [("⬅️ К корзине", "cart"), ("🏠 Главное меню", "menu")]]
    return text, kb(*buttons)


async def show_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        user_id = user.id
    text, markup = await checkout_view(user_id, data.get("promo_code"), bool(data.get("use_bonus")))
    await render(callback, text, markup, "checkout")


@router.callback_query(F.data == "checkout")
async def checkout(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await show_checkout(callback, state)


@router.callback_query(F.data == "checkout:promo")
async def enter_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Checkout.waiting_promo)
    await render(callback, PROMO_PROMPT, PROMO_BACK, "checkout")


PROMO_PROMPT = "<b>Промокод</b>\nОтправьте код одним сообщением.\nЛатинские буквы, цифры, «-» или «_»."
PROMO_BACK = kb([("⬅️ Вернуться к оформлению", "checkout")])


@router.message(Checkout.waiting_promo)
async def save_promo(message: Message, state: FSMContext) -> None:
    code = order_service.normalize_promo_code(message.text)
    if not code or len(code) > 40 or not all(char.isalnum() or char in "-_" for char in code):
        await reply(message, PROMO_PROMPT + "\n\n⚠️ Код введён некорректно, попробуйте ещё раз.", PROMO_BACK, "checkout")
        return
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, message)
        promo = await session.scalar(select(PromoCode).where(PromoCode.code == code))
        if not promo or not order_service.is_promo_available(promo):
            await reply(message, PROMO_PROMPT + f"\n\n⚠️ Промокод <b>{escape(code)}</b> не действует или его лимит исчерпан.", PROMO_BACK, "checkout")
            return
        user_id = user.id
    await state.update_data(promo_code=code)
    await state.set_state(None)
    data = await state.get_data()
    text, markup = await checkout_view(user_id, code, bool(data.get("use_bonus")))
    await reply(message, text, markup, "checkout")


@router.callback_query(F.data == "checkout:clear_promo")
async def clear_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(promo_code=None)
    await show_checkout(callback, state)


@router.callback_query(F.data == "checkout:bonus")
async def toggle_bonus(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(use_bonus=not bool(data.get("use_bonus")))
    await show_checkout(callback, state)


@router.callback_query(F.data.startswith("pay:"))
async def pay_choice(callback: CallbackQuery, state: FSMContext) -> None:
    await show_checkout(callback, state)


@router.callback_query(F.data.startswith("create:"))
async def create(callback: CallbackQuery, state: FSMContext) -> None:
    await show_checkout(callback, state)


async def order_details(callback: CallbackQuery, order: Order) -> None:
    items = "\n".join(f"• {escape(x.product_name_snapshot)} × {x.quantity} — {catalog_service.money(x.total_minor)}" for x in order.items)
    method = "Банковская карта" if order.payment_method == "card" else "СБП"
    buttons = []
    if order.status == "pending_payment":
        buttons.append([("❌ Отменить заказ", f"cancel:{order.id}")])
    buttons += [[("📦 Мои заказы", "orders:1")], nav("profile")]
    discounts = []
    if order.promo_discount_minor:
        discounts.append(f"Промокод {escape(order.promo_code or '')}: −{catalog_service.money(order.promo_discount_minor)}")
    if order.bonus_used_minor:
        discounts.append(f"Списано бонусов: −{catalog_service.money(order.bonus_used_minor)}")
    if order.bonus_accrued_minor and order.status == "paid":
        discounts.append(f"Начислено бонусов: +{catalog_service.money(order.bonus_accrued_minor)}")
    discount_text = ("\n" + "\n".join(discounts)) if discounts else ""
    await render(callback, f"🧾 <b>Заказ {escape(order.public_number)}</b>\nСтатус: <b>{status_text(order.status)}</b>\nОплата: {method}\n\n{items}\n\nСумма товаров: <b>{catalog_service.money(order.subtotal_minor or order.total_minor)}</b>{discount_text}\nИтого: <b>{catalog_service.money(order.total_minor)}</b>", kb(*buttons), "orders")


@router.callback_query(F.data.startswith("paid:"))
async def payment_callback_disabled(callback: CallbackQuery, state: FSMContext) -> None:
    await show_checkout(callback, state)


@router.callback_query(F.data.startswith("cancel:"))
async def transition(callback: CallbackQuery) -> None:
    _, raw_id = callback.data.split(":")
    target = "cancelled"
    try:
        async with SessionLocal() as session, session.begin():
            user = await user_for(session, callback)
            order, changed = await order_service.transition_order(session, int(raw_id), user.id, target)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            order = await order_service.get_order(session, order.id, user.id)
        await order_details(callback, order)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        text = f"👤 <b>Личный кабинет</b>\n\nИмя: {escape(user.first_name or 'не указано')}\nUsername: @{escape(user.username or 'не указан')}\nTelegram ID: <code>{user.telegram_id}</code>\nРегистрация: {user.created_at.strftime('%d.%m.%Y') if user.created_at else 'сейчас'}\n\n<b>Бонусный баланс: {catalog_service.money(user.bonus_balance_minor)}</b>\nНачисляем 2% от суммы каждого успешно оплаченного заказа. Бонусами можно оплатить до 100% следующего заказа."
    await render(callback, text, kb([("📦 Мои заказы", "orders:1"), ("🛒 Корзина", "cart")], [("💬 Поддержка", "support")], nav()), "profile")


@router.callback_query(F.data.startswith("orders:"))
async def orders(callback: CallbackQuery) -> None:
    page = max(1, int(callback.data.split(":")[1]))
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        total = await session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) or 0
        rows = list((await session.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE))).all())
    if not rows:
        await render(callback, "<b>Заказов пока нет</b>\nОформленные заказы и их статусы появятся здесь.", kb([("🛍 В каталог", "menu")], nav("profile")), "orders")
        return
    pages = math.ceil(total / PAGE_SIZE)
    buttons = [[(f"{x.public_number} · {status_text(x.status)}", f"order:{x.id}")] for x in rows]
    buttons.append([("⬅️", f"orders:{page-1}") if page > 1 else (f"{page}/{pages}", "noop"), ("➡️", f"orders:{page+1}") if page < pages else (f"{page}/{pages}", "noop")])
    buttons.append(nav("profile"))
    await render(callback, f"<b>Мои заказы</b>\nВсего {total} · страница {page} из {pages}", kb(*buttons), "orders")


@router.callback_query(F.data.startswith("order:"))
async def order(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        entity = await order_service.get_order(session, order_id, user.id)
    if not entity:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await order_details(callback, entity)


@router.callback_query((F.data == "documents") | (F.data == "rules"))
async def documents(callback: CallbackQuery) -> None:
    await render(
        callback,
        "<b>Документы и правила</b>\n"
        "Здесь всегда доступны условия использования, обработки данных, цены и правила оплаты.",
        documents_keyboard(),
        "docs",
    )


@router.callback_query(F.data.startswith("doc:"))
async def document(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    texts = {
        "privacy": privacy_policy,
        "terms": terms_of_service,
        "prices": prices_and_tariffs,
        "payments": payment_and_refunds,
        "rules": service_rules,
    }
    builder = texts.get(code)
    if not builder:
        await callback.answer("Документ не найден", show_alert=True)
        return
    await render(callback, builder(settings), kb([("⬅️ К документам", "documents"), ("🏠 Меню", "menu")]), "docs")


@router.callback_query(F.data == "support")
async def support(callback: CallbackQuery) -> None:
    contact = escape(settings.support_contact) if settings.has_public_support_contact else "Напишите обращение через кнопку «Новое обращение»."
    await render(callback, f"<b>Поддержка</b>\n{contact}\n\nЕсли заказ уже создан, укажите его номер — так мы быстрее найдём историю. Никогда не отправляйте пароли, коды подтверждения или реквизиты карты.", kb(nav()))


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
