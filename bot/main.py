import asyncio
import logging
from time import perf_counter

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent
from sqlalchemy import text

from bot.handlers import admin, store, support
from bot.keyboards.inline import main_menu
from config import get_settings
from database.session import engine


logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Открыть магазин"),
    BotCommand(command="help", description="Как пользоваться ботом"),
    BotCommand(command="support", description="Написать в поддержку"),
    BotCommand(command="paysupport", description="Вопрос по оплате"),
    BotCommand(command="documents", description="Документы и правила"),
    BotCommand(command="privacy", description="Политика конфиденциальности"),
    BotCommand(command="agreement", description="Пользовательское соглашение"),
    BotCommand(command="offer", description="Публичная оферта"),
    BotCommand(command="returns", description="Возвраты и отмена"),
    BotCommand(command="contacts", description="Контакты поддержки"),
    BotCommand(command="prices", description="Цены и тарифы"),
]


async def configure_commands(bot: Bot) -> None:
    """Publish the command menu shown when a customer types '/' in Telegram."""
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        # A temporary Telegram API failure must not stop the shop from starting.
        logger.exception("Could not update Telegram command menu")


class SlowUpdateMiddleware(BaseMiddleware):
    """Expose slow Telegram API/database work in container logs."""

    async def __call__(self, handler, event, data):
        started = perf_counter()
        try:
            return await handler(event, data)
        finally:
            elapsed = perf_counter() - started
            if elapsed >= 1:
                logger.warning("Slow update handled in %.2f seconds", elapsed)


async def recover_stale_screen(event: ErrorEvent) -> bool:
    """An old or broken button should land the user on the main menu, not a dead spinner."""
    logger.error("Update failed", exc_info=event.exception)
    callback = event.update.callback_query
    if not callback or not callback.message:
        return False
    try:
        await callback.answer("Экран устарел — открыл главное меню")
    except Exception:
        pass
    try:
        await store.render(callback, await store.main_text(), main_menu(), "menu")
    except Exception:
        logger.exception("Could not recover the panel")
    return True


async def keep_warm(bot: Bot, interval: float = 60) -> None:
    """Keep Telegram API and database connections alive during idle periods."""
    while True:
        await asyncio.sleep(interval)
        started = perf_counter()
        try:
            await bot.get_me()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Keep-warm ping failed")
        else:
            elapsed = perf_counter() - started
            if elapsed >= 1:
                logger.warning("Slow keep-warm ping: %.2f seconds", elapsed)


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is empty; copy .env.example to .env and set it")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(support.router)
    dp.include_router(store.router)
    dp.update.outer_middleware(SlowUpdateMiddleware())
    dp.errors.register(recover_stale_screen)
    await bot.delete_webhook(drop_pending_updates=False)
    await configure_commands(bot)
    warm_task = asyncio.create_task(keep_warm(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        warm_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
