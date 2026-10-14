from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from bot.handlers.store import render, status_text
from bot.keyboards.inline import kb, nav
from bot.states import Support
from config import get_settings
from database.models import AuditLog, Order, Product, SupportMessage, SupportTicket, User
from database.session import SessionLocal
from services import catalog_service, order_service
from bot.handlers.support import transcript

router = Router()
settings = get_settings()


def is_admin(telegram_id: int | None) -> bool:
    return bool(telegram_id and telegram_id in settings.admins)


def admin_menu():
    return kb([("📦 Последние заказы", "adm_orders")], [("💬 Открытые обращения", "adm_support")], [("📊 Статистика", "adm_stats")], [("🗂 Как обновить каталог", "adm_import")], nav())


@router.message(Command("admin"))
async def admin(message: Message) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        return
    await message.answer("<b>Админ-меню</b>", reply_markup=admin_menu(), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_"))
async def callbacks(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if callback.data == "adm_orders":
        async with SessionLocal() as session:
            rows = list((await session.scalars(select(Order).order_by(Order.created_at.desc()).limit(10))).all())
        text = "<b>Последние заказы</b>"
        buttons = [[(f"{x.public_number} · {status_text(x.status)}", f"admord:{x.id}")] for x in rows]
        await render(callback, text if rows else text + "\nНет заказов", kb(*buttons, [("⬅️ В админку", "adm_home")]) if rows else admin_menu())
    elif callback.data == "adm_home":
        await render(callback, "<b>Админ-меню</b>", admin_menu())
    elif callback.data.startswith("admord:"):
        order_id = int(callback.data.split(":")[1])
        async with SessionLocal() as session:
            order = await order_service.get_order(session, order_id)
        if not order:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        lines = "\n".join(f"• {escape(x.product_name_snapshot)} × {x.quantity}" for x in order.items)
        buttons = []
        if order.status == "pending_payment":
            buttons.append([("Отменить заказ", f"admset:{order.id}:cancelled")])
        buttons.append([("⬅️ К заказам", "adm_orders")])
        await render(callback, f"<b>{escape(order.public_number)}</b>\n{status_text(order.status)}\n{lines}\nИтого: {catalog_service.money(order.total_minor)}", kb(*buttons))
    elif callback.data.startswith("admset:"):
        _, order_id, target = callback.data.split(":")
        try:
            async with SessionLocal() as session, session.begin():
                order, changed = await order_service.transition_order(session, int(order_id), None, target)
                if not order:
                    await callback.answer("Заказ не найден", show_alert=True)
                    return
                if changed:
                    session.add(AuditLog(actor_telegram_id=callback.from_user.id, action="order_status_changed", entity=f"order:{order.id}", payload={"status": target}))
            await callback.answer("Статус изменён" if changed else "Статус уже установлен")
            await render(callback, "Статус обновлён. Откройте заказ снова для просмотра.", kb([("Открыть заказ", f"admord:{order_id}")], [("⬅️ К заказам", "adm_orders")]))
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
    elif callback.data == "adm_stats":
        async with SessionLocal() as session:
            users = await session.scalar(select(func.count()).select_from(User)) or 0
            products = await session.scalar(select(func.count()).select_from(Product).where(Product.active)) or 0
            statuses = list((await session.execute(select(Order.status, func.count(), func.coalesce(func.sum(Order.total_minor), 0)).group_by(Order.status))).all())
        lines = [f"Пользователи: <b>{users}</b>", f"Активные товары: <b>{products}</b>", "Заказы:"]
        lines += [f"• {status_text(status)}: {count}; {catalog_service.money(total)}" for status, count, total in statuses]
        await render(callback, "<b>Статистика</b>\n" + "\n".join(lines), admin_menu())
    elif callback.data == "adm_support":
        async with SessionLocal() as session:
            tickets = list((await session.scalars(select(SupportTicket).where(SupportTicket.status == "open").order_by(SupportTicket.updated_at.desc()).limit(30))).all())
        if not tickets:
            await render(callback, "<b>Открытые обращения</b>\nНет активных обращений.", admin_menu())
            return
        buttons = [[(f"{ticket.public_number} · открыто", f"supadm:{ticket.id}")] for ticket in tickets]
        buttons.append([("⬅️ В админку", "adm_home")])
        await render(callback, "<b>Открытые обращения</b>", kb(*buttons))
    elif callback.data == "adm_import":
        await render(
            callback,
            "<b>Каталог</b>\nТовары загружаются разово скриптом на сервере:\n"
            "<code>docker compose run --rm bot python -m scripts.snapshot_import --usd-rub 95 --markup 15</code>\n\n"
            "Цены дальше меняются в веб-админке, раздел «Каталог».",
            admin_menu(),
        )


@router.callback_query(F.data.startswith("supadm:"))
async def support_admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id if callback.from_user else None):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1] if len(parts) == 3 else "view"
    ticket_id = int(parts[-1])
    if action == "reply":
        await state.set_state(Support.admin_reply)
        await state.update_data(ticket_id=ticket_id)
        await render(callback, "Напишите ответ пользователю одним сообщением. Он будет отправлен в его чат и сохранён в истории.", kb([("⬅️ К обращению", f"supadm:{ticket_id}")]))
        return
    async with SessionLocal() as session, session.begin():
        ticket = await session.scalar(select(SupportTicket).options(selectinload(SupportTicket.messages)).where(SupportTicket.id == ticket_id))
        if not ticket:
            await callback.answer("Обращение не найдено", show_alert=True)
            return
        if action == "close" and ticket.status == "open":
            ticket.status = "closed"
        user = await session.get(User, ticket.user_id)
    if action == "close":
        await render(callback, "Обращение закрыто.", kb([("⬅️ К обращениям", "adm_support")]))
        if user:
            try:
                await callback.bot.send_message(user.telegram_id, f"Обращение {ticket.public_number} закрыто. Если вопрос остался — создайте новое через «Поддержка».")
            except Exception:
                pass
        return
    customer = f"Клиент: {escape(user.first_name or 'без имени')} · <code>{user.telegram_id}</code>" if user else "Клиент удалён"
    buttons = []
    if ticket.status == "open":
        buttons.append([("💬 Ответить", f"supadm:reply:{ticket.id}"), ("✅ Закрыть", f"supadm:close:{ticket.id}")])
    buttons.append([("⬅️ К обращениям", "adm_support")])
    await render(callback, customer + "\n\n" + transcript(ticket, admin_view=True), kb(*buttons))


@router.message(Support.admin_reply)
async def support_admin_reply(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        await state.clear()
        return
    text = (message.text or "").strip()[:2000]
    if not text:
        await message.answer("Отправьте текстовое сообщение до 2 000 символов.")
        return
    ticket_id = (await state.get_data()).get("ticket_id")
    async with SessionLocal() as session, session.begin():
        ticket = await session.scalar(select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update())
        if not ticket or ticket.status != "open":
            await state.clear()
            await message.answer("Обращение закрыто или не найдено.")
            return
        customer = await session.get(User, ticket.user_id)
        session.add(SupportMessage(ticket_id=ticket.id, sender_kind="admin", sender_telegram_id=message.from_user.id, body=text))
        ticket_number = ticket.public_number
        session.add(AuditLog(actor_telegram_id=message.from_user.id, action="support_reply", entity=f"ticket:{ticket.id}", payload={"ticket": ticket_number}))
    await state.clear()
    await message.answer(f"Ответ отправлен в {ticket_number}.", reply_markup=kb([("Открыть обращение", f"supadm:{ticket_id}")]))
    if customer:
        try:
            await message.bot.send_message(customer.telegram_id, f"<b>Ответ поддержки · {ticket_number}</b>\n\n{escape(text)}", parse_mode="HTML", reply_markup=kb([("Открыть обращение", f"sup:ticket:{ticket_id}")]))
        except Exception:
            await message.answer("Ответ сохранён, но Telegram не доставил его пользователю.")
