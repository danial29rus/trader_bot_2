from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.handlers.store import render, reply, user_for
from bot.keyboards.inline import kb, nav
from bot.states import Support
from config import get_settings
from database.models import SupportMessage, SupportTicket, User
from database.session import SessionLocal

router = Router()
settings = get_settings()


def ticket_status(status: str) -> str:
    return "открыто" if status == "open" else "закрыто"


def customer_support_keyboard():
    return kb([("✍️ Новое обращение", "sup:new")], [("📨 Мои обращения", "sup:mine")], nav())


@router.message(Command("support", "paysupport"))
async def support_command(message: Message) -> None:
    await reply(message, "💬 <b>Поддержка</b>\nОпишите вопрос — оператор увидит обращение в этом же боте и ответит сюда. Не отправляйте пароли, коды подтверждения и данные карты.", customer_support_keyboard(), "support")


async def notify_admins(bot: Bot, ticket: SupportTicket, text: str) -> None:
    for admin_id in settings.admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb([("Открыть обращение", f"supadm:{ticket.id}")]), parse_mode="HTML")
        except Exception:
            # A blocked administrator must not prevent saving the customer's request.
            continue


async def customer_ticket(session, ticket_id: int, user_id: int) -> SupportTicket | None:
    stmt = select(SupportTicket).options(selectinload(SupportTicket.messages)).where(SupportTicket.id == ticket_id, SupportTicket.user_id == user_id)
    return await session.scalar(stmt)


def transcript(ticket: SupportTicket, admin_view: bool = False) -> str:
    lines = [f"<b>Обращение {escape(ticket.public_number)}</b>", f"Статус: {ticket_status(ticket.status)}", ""]
    for item in ticket.messages[-12:]:
        author = "👤 Вы" if item.sender_kind == "customer" else "🛟 Поддержка"
        lines.append(f"<b>{author}</b>\n{escape(item.body)}")
    if len(ticket.messages) > 12:
        lines.append("\n<i>Показаны последние 12 сообщений.</i>")
    return "\n\n".join(lines)


@router.callback_query(F.data == "support")
async def support_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, "💬 <b>Поддержка</b>\nОпишите вопрос — оператор увидит обращение в этом же боте и ответит сюда. Не отправляйте пароли, коды подтверждения и данные карты.", customer_support_keyboard(), "support")


@router.callback_query(F.data == "sup:new")
async def support_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Support.waiting_message)
    await state.update_data(ticket_id=None)
    await render(callback, "<b>Новое обращение</b>\nНапишите одним сообщением, что произошло. Если вопрос о заказе — добавьте его номер.", kb(nav("support")), "support")


@router.callback_query(F.data == "sup:mine")
async def support_mine(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        tickets = list((await session.scalars(select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.updated_at.desc()).limit(20))).all())
    if not tickets:
        await render(callback, "<b>Обращений пока нет</b>\nЕсли что-то пошло не так — создайте новое обращение.", customer_support_keyboard(), "support")
        return
    buttons = [[(f"{ticket.public_number} · {ticket_status(ticket.status)}", f"sup:ticket:{ticket.id}")] for ticket in tickets]
    buttons.append([("✍️ Новое обращение", "sup:new")])
    buttons.append(nav("support"))
    await render(callback, "<b>Мои обращения</b>", kb(*buttons), "support")


@router.callback_query(F.data.startswith("sup:ticket:"))
async def support_ticket(callback: CallbackQuery) -> None:
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        ticket = await customer_ticket(session, ticket_id, user.id)
    if not ticket:
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    buttons = []
    if ticket.status == "open":
        buttons.append([("✍️ Дописать", f"sup:write:{ticket.id}"), ("✅ Закрыть", f"sup:close:{ticket.id}")])
    buttons.append([("⬅️ К обращениям", "sup:mine")])
    await render(callback, transcript(ticket), kb(*buttons), "support")


@router.callback_query(F.data.startswith("sup:write:"))
async def support_write(callback: CallbackQuery, state: FSMContext) -> None:
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(Support.waiting_message)
    await state.update_data(ticket_id=ticket_id)
    await render(callback, "Напишите сообщение для обращения. Оно будет отправлено оператору.", kb([("⬅️ К обращению", f"sup:ticket:{ticket_id}")]), "support")


@router.message(Support.waiting_message)
async def customer_message(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()[:2000]
    if not text:
        await reply(message, "<b>Новое обращение</b>\nОтправьте текстовое сообщение до 2 000 символов.", kb(nav("support")), "support")
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, message)
        ticket = await customer_ticket(session, ticket_id, user.id) if ticket_id else None
        if ticket_id and (not ticket or ticket.status != "open"):
            await state.clear()
            await reply(message, "<b>Обращение уже закрыто</b>\nСоздайте новое — оператор увидит его сразу.", customer_support_keyboard(), "support")
            return
        if not ticket:
            ticket = SupportTicket(public_number=f"S-{uuid4().hex[:8].upper()}", user_id=user.id, status="open")
            session.add(ticket)
            await session.flush()
        session.add(SupportMessage(ticket_id=ticket.id, sender_kind="customer", sender_telegram_id=message.from_user.id, body=text))
        ticket.updated_at = datetime.now(timezone.utc)
        ticket_number = ticket.public_number
    await state.clear()
    await reply(message, f"✅ Обращение <b>{ticket_number}</b> отправлено.\nОтвет оператора придёт в этот чат.", customer_support_keyboard(), "support")
    await notify_admins(message.bot, ticket, f"<b>Новое сообщение в поддержке</b>\n{escape(ticket_number)}\nОт: {escape(message.from_user.full_name)}\n\n{escape(text[:600])}")


@router.callback_query(F.data.startswith("sup:close:"))
async def customer_close(callback: CallbackQuery) -> None:
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        ticket = await customer_ticket(session, ticket_id, user.id)
        if not ticket:
            await callback.answer("Обращение не найдено", show_alert=True)
            return
        if ticket.status == "open":
            ticket.status, ticket.closed_at, ticket.updated_at = "closed", datetime.now(timezone.utc), datetime.now(timezone.utc)
    await render(callback, "Обращение закрыто. При необходимости можно создать новое.", customer_support_keyboard(), "support")
