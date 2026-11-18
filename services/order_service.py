from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import CartItem, Order, OrderItem, Payment, Product, PromoCode, User

ALLOWED_TRANSITIONS = {"pending_payment": {"cancelled"}, "paid_test": set(), "cancelled": set()}
BONUS_PERCENT = 2


def normalize_promo_code(value: str | None) -> str | None:
    code = "".join((value or "").upper().split())
    return code or None


def is_promo_available(promo: PromoCode, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return promo.active and (promo.expires_at is None or promo.expires_at > now) and (promo.usage_limit is None or promo.used_count < promo.usage_limit)


async def create_order(
    session: AsyncSession,
    user_id: int,
    payment_method: str,
    promo_code: str | None = None,
    use_bonus: bool = False,
) -> Order:
    if payment_method not in {"card", "sbp"}:
        raise ValueError("Недопустимый метод оплаты")
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user:
        raise ValueError("Пользователь не найден")
    lines = list((await session.scalars(select(CartItem).options(selectinload(CartItem.product)).where(CartItem.user_id == user_id).with_for_update())).all())
    if not lines:
        raise ValueError("Корзина пуста")
    subtotal = 0
    order = Order(public_number=f"T-{uuid4().hex[:10].upper()}", user_id=user_id, status="pending_payment", payment_method=payment_method, subtotal_minor=0, total_minor=0, currency="RUB")
    session.add(order)
    await session.flush()
    for line in lines:
        product: Product = line.product
        if not product.active or product.currency != "RUB":
            raise ValueError("Один из товаров больше нельзя оформить")
        amount = product.price_minor * line.quantity
        subtotal += amount
        session.add(OrderItem(order_id=order.id, product_id=product.id, product_name_snapshot=product.name, configuration_snapshot=line.configuration, unit_price_minor=product.price_minor, quantity=line.quantity, total_minor=amount))
        await session.delete(line)
    promo: PromoCode | None = None
    normalized_code = normalize_promo_code(promo_code)
    if normalized_code:
        promo = await session.scalar(select(PromoCode).where(PromoCode.code == normalized_code).with_for_update())
        if not promo or not is_promo_available(promo):
            raise ValueError("Промокод недействителен или его лимит исчерпан")
        promo.used_count += 1  # Reserve one use until payment succeeds or the order is cancelled.
        order.promo_code = promo.code
        order.promo_discount_minor = subtotal * promo.discount_percent // 100
    after_promo = subtotal - order.promo_discount_minor
    if use_bonus:
        order.bonus_used_minor = min(user.bonus_balance_minor, after_promo)
        user.bonus_balance_minor -= order.bonus_used_minor
    order.subtotal_minor = subtotal
    order.total_minor = after_promo - order.bonus_used_minor
    order.bonus_accrued_minor = order.total_minor * BONUS_PERCENT // 100
    session.add(Payment(order_id=order.id, method=payment_method, status="pending", amount_minor=order.total_minor, is_test=False, reference=f"payment-{uuid4().hex}"))
    await session.flush()
    return order


async def transition_order(session: AsyncSession, order_id: int, user_id: int | None, target: str) -> tuple[Order | None, bool]:
    stmt = select(Order).where(Order.id == order_id).with_for_update()
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    order = await session.scalar(stmt)
    if not order:
        return None, False
    if order.status == target:
        return order, False
    if target not in ALLOWED_TRANSITIONS.get(order.status, set()):
        raise ValueError("Этот переход статуса недопустим")
    if target == "cancelled":
        user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
        if user:
            user.bonus_balance_minor += order.bonus_used_minor
        if order.promo_code:
            promo = await session.scalar(select(PromoCode).where(PromoCode.code == order.promo_code).with_for_update())
            if promo and promo.used_count:
                promo.used_count -= 1
    order.status = target
    return order, True


async def mark_order_paid(session: AsyncSession, order_id: int) -> tuple[Order | None, bool]:
    """Call only after a verified, idempotent payment-provider status check."""
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order:
        return None, False
    if order.status == "paid":
        return order, False
    if order.status != "pending_payment":
        raise ValueError("Нельзя подтвердить оплату заказа в текущем статусе")
    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if user:
        user.bonus_balance_minor += order.bonus_accrued_minor
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    return order, True


async def record_mulenpay_status(session: AsyncSession, payment_id: int, provider_status: int) -> tuple[Order | None, bool]:
    """Persist a provider status and confirm the order only for MulenPay status 3."""
    payment = await session.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if not payment:
        return None, False
    payment.provider_status = provider_status
    payment.last_checked_at = datetime.now(timezone.utc)
    if provider_status != 3:
        return None, False
    order, changed = await mark_order_paid(session, payment.order_id)
    if changed:
        payment.status = "paid"
        payment.confirmed_at = datetime.now(timezone.utc)
    return order, changed


async def get_order(session: AsyncSession, order_id: int, user_id: int | None = None) -> Order | None:
    stmt = select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    return await session.scalar(stmt)
