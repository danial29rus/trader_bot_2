from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


def main_menu() -> InlineKeyboardMarkup:
    """Only show storefront sections that this curated catalogue actually imports."""
    rows = [
        [("⚡ Донат в игры", "cat_type:game_topup"), ("🟥 Roblox", "cat_type:roblox")],
        [("💳 Игровые карты", "cat_type:gift_card"), ("🔑 Ключи игр", "cat_type:game_key")],
        [("🎮 Мои игры", "accounts"), ("🔎 Поиск", "search")],
        [("🛒 Корзина", "cart"), ("📦 Заказы", "orders:1")],
        [("👤 Кабинет", "profile"), ("💬 Поддержка", "support")],
        [("📄 Документы", "documents")],
    ]
    return kb(*rows)


def nav(back: str = "menu") -> list[tuple[str, str]]:
    return [("⬅️ Назад", back), ("🏠 Меню", "menu")]
