"""Poll pending MulenPay payments and deliver confirmed-order notifications."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select

from config import get_settings
from database.models import Order, Payment, User
from database.session import SessionLocal
from integrations.mulenpay_client import MulenPayClient, MulenPayError
from services.order_service import record_mulenpay_status

logger = logging.getLogger(__name__)


async def check_once(client: MulenPayClient, bot: Bot) -> None:
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(Payment.id, Payment.provider_payment_id).join(Order).where(
                Payment.method == "mulenpay", Payment.status == "pending",
                Payment.provider_payment_id.is_not(None), Order.status == "pending_payment",
            ).limit(100)
        )).all())
    for payment_id, provider_id in rows:
        try:
            provider_status = await client.get_payment_status(provider_id)
        except MulenPayError:
            logger.exception("Could not check MulenPay payment %s", provider_id)
            continue
        async with SessionLocal() as session, session.begin():
            order, changed = await record_mulenpay_status(session, payment_id, provider_status)
            if changed and order:
                user = await session.get(User, order.user_id)
                notice = (user.telegram_id, order.public_number, order.status) if user else None
            else:
                notice = None
        if notice:
            try:
                if notice[2] == "paid":
                    text = f"✅ <b>Оплата заказа {notice[1]} подтверждена.</b>\nЗаказ передан в обработку."
                else:
                    text = f"Платёж по заказу {notice[1]} отменён. Заказ отменён."
                await bot.send_message(notice[0], text)
            except Exception:
                logger.exception("Could not notify customer about order %s", notice[1])


async def main() -> None:
    settings = get_settings()
    if not settings.mulenpay_configured:
        raise RuntimeError("MulenPay is not configured")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is empty")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    client = MulenPayClient(settings)
    try:
        while True:
            await check_once(client, bot)
            await asyncio.sleep(max(5, settings.mulenpay_poll_interval_seconds))
    finally:
        await client.aclose()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
