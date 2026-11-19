from __future__ import annotations

import math
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from typing import Annotated
from uuid import uuid4

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import UUID4, BaseModel, Field
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import selectinload

from admin_api.settings import get_admin_settings
from database.models import Order, OrderItem, Payment, Product, PromoCode, SupportMessage, SupportTicket, User
from database.session import SessionLocal
from config import get_settings
from services.legal import contacts, payment_and_refunds, prices_and_tariffs, privacy_policy, returns_policy, terms_of_service
from services.order_service import is_promo_available, normalize_promo_code

app = FastAPI(title="HonestPlay Admin API", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
password_hasher = PasswordHasher()

PUBLIC_DOCUMENTS = {
    "privacy.html": privacy_policy,
    "agreement.html": terms_of_service,
    "offer.html": payment_and_refunds,
    "returns.html": returns_policy,
    "contacts.html": contacts,
    "prices.html": prices_and_tariffs,
}


def public_document_page(content: str) -> str:
    # Legal builders escape configuration values themselves and use only the
    # Telegram subset of HTML tags, which browsers can render directly.
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Документы</title><style>
body{{margin:0;background:#10131a;color:#e9edf5;font:16px/1.6 system-ui,-apple-system,sans-serif}}main{{max-width:760px;margin:0 auto;padding:36px 20px 56px}}b{{display:block;font-size:1.05em;margin-top:1.45em}}main>b:first-child{{font-size:1.7em;margin-top:0}}a{{color:#82b7ff}}footer{{margin-top:36px;color:#aeb8c8;font-size:.9em}}
</style></head><body><main>{content}<footer>Актуальная редакция публикуется с указанной датой.</footer></main></body></html>"""


@app.get("/docs/{page}", response_class=HTMLResponse, include_in_schema=False)
async def public_document(page: str) -> HTMLResponse:
    builder = PUBLIC_DOCUMENTS.get(page)
    if not builder:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return HTMLResponse(public_document_page(builder(get_settings())), headers={"Cache-Control": "no-store"})


class LoginPayload(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class ReplyPayload(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class PromoCodePayload(BaseModel):
    code: str = Field(min_length=3, max_length=40)
    discount_percent: int = Field(ge=1, le=100)
    usage_limit: int | None = Field(default=None, ge=1, le=1_000_000)


class PromoCodeStatePayload(BaseModel):
    active: bool


class ProductStatePayload(BaseModel):
    active: bool


class ProductPricePayload(BaseModel):
    price_rub: Decimal = Field(gt=0, le=10_000_000)


class RepricePayload(BaseModel):
    product_type: str = ""
    rub_per_usd: Decimal = Field(gt=0, le=10_000)
    markup_percent: Decimal = Field(ge=-50, le=500)
    include_locked: bool = False


class ManualOrderPayload(BaseModel):
    user_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=100)
    promo_code: str | None = Field(default=None, max_length=40)
    use_bonus: bool = False


class PublicPaymentCreatePayload(BaseModel):
    amount: Decimal = Field(gt=0, le=10_000_000, decimal_places=2)
    uuid: UUID4
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    description: str = Field(default="Оплата заказа", min_length=1, max_length=255)


async def require_public_payments_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    configured_key = get_settings().public_payments_api_key
    if not configured_key:
        raise HTTPException(status_code=503, detail="Публичный платежный API не настроен")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode("utf-8"), configured_key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Неверный API-ключ")


def as_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def user_data(user: User | None) -> dict:
    if not user:
        return {"id": None, "telegram_id": None, "username": None, "first_name": "Удалённый пользователь", "email": None, "bonus_balance_minor": 0}
    return {"id": user.id, "telegram_id": user.telegram_id, "username": user.username, "first_name": user.first_name, "email": user.email, "bonus_balance_minor": user.bonus_balance_minor}


def promo_data(item: PromoCode) -> dict:
    return {
        "id": item.id,
        "code": item.code,
        "discount_percent": item.discount_percent,
        "usage_limit": item.usage_limit,
        "used_count": item.used_count,
        "active": item.active,
        "expires_at": as_iso(item.expires_at),
        "created_at": as_iso(item.created_at),
    }


def product_data(item: Product) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "product_type": item.product_type,
        "region": item.region,
        "price_minor": item.price_minor,
        "currency": item.currency,
        "supplier_price_usd": str(item.supplier_price_usd) if item.supplier_price_usd is not None else None,
        "price_locked": item.price_locked,
        "active": item.active,
    }


async def require_admin(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    settings = get_admin_settings()
    if not settings.configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Администратор ещё не настроен")
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация")
    try:
        payload = jwt.decode(credentials.credentials, settings.admin_jwt_secret, algorithms=["HS256"])
        if payload.get("sub") != settings.admin_username:
            raise ValueError("unknown account")
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла") from exc
    return settings.admin_username


Admin = Annotated[str, Depends(require_admin)]
PaymentsApi = Annotated[None, Depends(require_public_payments_key)]


async def send_telegram(user: User, text: str) -> None:
    token = get_admin_settings().bot_token
    if not token:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": user.telegram_id, "text": text, "parse_mode": "HTML"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        # The response is preserved in the database even if a customer blocked the bot.
        return


def ticket_data(ticket: SupportTicket, customer: User | None, message_count: int | None = None) -> dict:
    return {
        "id": ticket.id,
        "public_number": ticket.public_number,
        "status": ticket.status,
        "created_at": as_iso(ticket.created_at),
        "updated_at": as_iso(ticket.updated_at),
        "closed_at": as_iso(ticket.closed_at),
        "customer": user_data(customer),
        "message_count": message_count if message_count is not None else len(ticket.messages),
    }


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status() -> dict:
    return {"configured": get_admin_settings().configured}


@app.post("/api/auth/login")
async def login(payload: LoginPayload) -> dict:
    settings = get_admin_settings()
    if not settings.configured:
        raise HTTPException(status_code=503, detail="Сначала настройте учётную запись администратора")
    valid = False
    if payload.username == settings.admin_username:
        try:
            valid = password_hasher.verify(settings.admin_password_hash, payload.password)
        except (VerifyMismatchError, InvalidHashError):
            valid = False
    if not valid:
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": settings.admin_username, "iat": now, "exp": now + timedelta(hours=8)}, settings.admin_jwt_secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer", "expires_in": 8 * 60 * 60}


@app.post("/api/public/payments", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_public_payments_key)])
async def create_public_payment(payload: PublicPaymentCreatePayload) -> dict:
    settings = get_settings()
    if not settings.mulenpay_configured:
        raise HTTPException(status_code=503, detail="MulenPay не настроен")
    amount_minor_decimal = payload.amount * 100
    if amount_minor_decimal != amount_minor_decimal.to_integral_value():
        raise HTTPException(status_code=422, detail="Сумма должна быть указана в рублях и копейках")
    amount_minor = int(amount_minor_decimal)
    payment_uuid = str(payload.uuid)
    email = payload.email.strip().casefold()
    async with SessionLocal() as session, session.begin():
        # Serialize same-UUID checks and local order registration in this transaction.
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(payment_uuid))))
        existing = await session.scalar(select(Payment).where(Payment.reference == payment_uuid))
        if existing:
            order = await session.get(Order, existing.order_id)
            if existing.method != "mulenpay" or not order:
                raise HTTPException(status_code=409, detail="UUID уже используется")
            if existing.amount_minor != amount_minor:
                raise HTTPException(status_code=409, detail="UUID уже зарегистрирован с другой суммой")
            customer = await session.get(User, order.user_id)
            if customer and (customer.email or "").casefold() != email:
                raise HTTPException(status_code=409, detail="UUID уже зарегистрирован для другого email")
            if existing.payment_url and existing.provider_payment_id:
                return {
                    "uuid": existing.reference, "amount": str(payload.amount),
                    "status": order.status, "payment_status": existing.status,
                    "payment_url": existing.payment_url, "provider_payment_id": existing.provider_payment_id,
                }
            if existing.status != "pending" or order.status != "pending_payment":
                raise HTTPException(status_code=409, detail="Платёж по этому UUID уже закрыт")
            order_id = order.id
        else:
            guests = list((await session.scalars(
                select(User).where(
                    User.telegram_id < 0,
                    User.first_name == "API customer",
                    func.lower(User.email) == email,
                ).order_by(User.id).with_for_update()
            )).all())
            guest = guests[0] if guests else None
            if not guest:
                guest_telegram_id = -int(hashlib.sha256(f"honestplay-api:{email}".encode()).hexdigest()[:15], 16)
                guest = await session.scalar(select(User).where(User.telegram_id == guest_telegram_id).with_for_update())
            if not guest:
                guest = User(telegram_id=guest_telegram_id, first_name="API customer", email=email)
                session.add(guest)
                await session.flush()
            elif guest.email != email:
                guest.email = email

            # Older API calls created one guest per UUID. Move their productless
            # external orders under the email's canonical API customer.
            if guests:
                old_guest_ids = [item.id for item in guests]
                await session.execute(
                    update(Order)
                    .where(
                        Order.user_id.in_(old_guest_ids),
                        Order.payment_method == "mulenpay",
                        ~exists(select(OrderItem.id).where(OrderItem.order_id == Order.id)),
                    )
                    .values(user_id=guest.id)
                )
            order = Order(
                public_number=payment_uuid, user_id=guest.id,
                status="pending_payment", payment_method="mulenpay", subtotal_minor=amount_minor,
                total_minor=amount_minor, currency="RUB",
            )
            session.add(order)
            await session.flush()
            payment = Payment(
                order_id=order.id, method="mulenpay", status="pending", amount_minor=amount_minor,
                is_test=False, reference=payment_uuid,
            )
            session.add(payment)
            await session.flush()
            order_id = order.id
    from integrations.mulenpay_client import MulenPayClient, MulenPayError
    client = MulenPayClient(settings)
    try:
        created = await client.create_payment(uuid=payment_uuid, amount_minor=amount_minor, description=payload.description, email=payload.email)
    except MulenPayError as exc:
        logger.error("MulenPay create-payment failed: %s", exc)
        async with SessionLocal() as session:
            order = await session.get(Order, order_id)
            if order:
                raise HTTPException(status_code=502, detail="Провайдер не создал платеж; повторите запрос с тем же UUID") from exc
        raise HTTPException(status_code=502, detail="Ошибка создания платежа") from exc
    finally:
        await client.aclose()
    async with SessionLocal() as session, session.begin():
        payment = await session.scalar(select(Payment).where(Payment.order_id == order_id).with_for_update())
        assert payment
        payment.provider_payment_id = created.id
        payment.payment_url = created.payment_url
    return {
        "uuid": payment_uuid, "amount": str(payload.amount),
        "status": "pending_payment", "payment_status": "pending",
        "payment_url": created.payment_url, "provider_payment_id": created.id,
    }


@app.get("/api/public/payments/{payment_uuid}", dependencies=[Depends(require_public_payments_key)])
async def public_payment_status(payment_uuid: str) -> dict:
    async with SessionLocal() as session:
        payment = await session.scalar(select(Payment).where(Payment.reference == payment_uuid))
        if not payment:
            raise HTTPException(status_code=404, detail="Платёж не найден")
        order = await session.get(Order, payment.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return {
        "uuid": payment.reference,
        "status": order.status, "payment_status": payment.status,
        "provider_status": payment.provider_status, "amount_minor": payment.amount_minor,
        "currency": order.currency, "created_at": as_iso(payment.created_at),
        "confirmed_at": as_iso(payment.confirmed_at),
    }


@app.get("/api/dashboard")
async def dashboard(_: Admin) -> dict:
    async with SessionLocal() as session:
        result = await session.execute(
            select(
                select(func.count(User.id)).scalar_subquery(),
                select(func.count(Order.id)).scalar_subquery(),
                select(func.count(SupportTicket.id)).where(SupportTicket.status == "open").scalar_subquery(),
                select(func.count(Product.id)).where(Product.active.is_(True)).scalar_subquery(),
            )
        )
        users, orders, tickets, products = result.one()
    return {"users": users, "orders": orders, "open_tickets": tickets, "active_products": products}


@app.get("/api/users")
async def users(_: Admin, query: str = "", limit: int = 100, offset: int = 0) -> dict:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    async with SessionLocal() as session:
        stmt = select(User)
        if query.strip():
            needle = query.strip()
            conditions = [User.first_name.ilike(f"%{needle}%"), User.username.ilike(f"%{needle}%"), User.email.ilike(f"%{needle}%")]
            if needle.lstrip("-").isdigit():
                conditions.append(User.telegram_id == int(needle))
            from sqlalchemy import or_
            stmt = stmt.where(or_(*conditions))
        total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list((await session.scalars(stmt.order_by(User.created_at.desc()).offset(offset).limit(limit))).all())
    return {"total": total, "items": [{**user_data(item), "created_at": as_iso(item.created_at)} for item in rows]}


@app.get("/api/orders")
async def orders(_: Admin, limit: int = 100, offset: int = 0, query: str = "") -> dict:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    async with SessionLocal() as session:
        stmt = select(Order).join(Payment, Payment.order_id == Order.id, isouter=True).join(User, User.id == Order.user_id)
        if query.strip():
            raw_query = query.strip()
            needle = f"%{raw_query}%"
            search = [Order.public_number.ilike(needle), Payment.reference.ilike(needle), Payment.payment_url.ilike(needle), User.email.ilike(needle)]
            if raw_query.isdigit():
                search.append(Payment.provider_payment_id == int(raw_query))
            stmt = stmt.where(or_(*search))
        total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list((await session.scalars(stmt.options(selectinload(Order.items), selectinload(Order.payment)).order_by(Order.created_at.desc()).offset(offset).limit(limit))).unique().all())
        customers = {item.user_id: await session.get(User, item.user_id) for item in rows}
    return {
        "total": total,
        "items": [
            {
                "id": item.id,
                "public_number": item.public_number,
                "uuid": item.payment.reference if item.payment else item.public_number,
                "status": item.status,
                "payment": ({
                    "method": item.payment.method,
                    "status": item.payment.status,
                    "amount_minor": item.payment.amount_minor,
                    "provider_payment_id": item.payment.provider_payment_id,
                    "provider_status": item.payment.provider_status,
                    "payment_url": item.payment.payment_url,
                    "created_at": as_iso(item.payment.created_at),
                    "confirmed_at": as_iso(item.payment.confirmed_at),
                    "last_checked_at": as_iso(item.payment.last_checked_at),
                } if item.payment else None),
                "subtotal_minor": item.subtotal_minor,
                "promo_code": item.promo_code,
                "promo_discount_minor": item.promo_discount_minor,
                "bonus_used_minor": item.bonus_used_minor,
                "bonus_accrued_minor": item.bonus_accrued_minor,
                "total_minor": item.total_minor,
                "currency": item.currency,
                "created_at": as_iso(item.created_at),
                "customer": user_data(customers[item.user_id]),
                "items": [{"name": line.product_name_snapshot, "quantity": line.quantity, "total_minor": line.total_minor} for line in item.items],
            }
            for item in rows
        ],
    }


@app.get("/api/products")
async def products(_: Admin, query: str = "", product_type: str = "", region: str = "", active: str = "", locked: str = "", limit: int = 200) -> dict:
    limit = min(max(limit, 1), 200)
    async with SessionLocal() as session:
        stmt = select(Product).order_by(Product.active.desc(), Product.name.asc()).limit(limit)
        if query.strip():
            stmt = stmt.where(Product.name.ilike(f"%{query.strip()}%"))
        if product_type:
            stmt = stmt.where(Product.product_type == product_type)
        if region.strip():
            stmt = stmt.where(Product.region.ilike(f"%{region.strip()}%"))
        if active == "true":
            stmt = stmt.where(Product.active.is_(True))
        elif active == "false":
            stmt = stmt.where(Product.active.is_(False))
        if locked == "true":
            stmt = stmt.where(Product.price_locked.is_(True))
        elif locked == "false":
            stmt = stmt.where(Product.price_locked.is_(False))
        rows = list((await session.scalars(stmt)).all())
    return {"items": [product_data(item) for item in rows]}


@app.post("/api/products/{product_id}/state")
async def set_product_state(product_id: int, payload: ProductStatePayload, _: Admin) -> dict:
    async with SessionLocal() as session, session.begin():
        item = await session.get(Product, product_id)
        if not item:
            raise HTTPException(status_code=404, detail="Товар не найден")
        item.active = payload.active
        await session.flush()
        data = product_data(item)
    return data


@app.post("/api/products/{product_id}/price")
async def set_product_price(product_id: int, payload: ProductPricePayload, _: Admin) -> dict:
    """A manual price is locked: later catalogue imports keep it."""
    async with SessionLocal() as session, session.begin():
        item = await session.get(Product, product_id)
        if not item:
            raise HTTPException(status_code=404, detail="Товар не найден")
        item.price_minor = int((payload.price_rub * 100).to_integral_value())
        item.currency = "RUB"
        item.price_locked = True
        await session.flush()
        data = product_data(item)
    return data


@app.post("/api/manual-orders", status_code=status.HTTP_201_CREATED)
async def create_manual_order(payload: ManualOrderPayload, _: Admin) -> dict:
    """Record an operator-created order without charging or ordering from a supplier."""
    async with SessionLocal() as session, session.begin():
        customer = await session.scalar(select(User).where(User.id == payload.user_id).with_for_update())
        product = await session.get(Product, payload.product_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if not product or not product.active or product.currency != "RUB":
            raise HTTPException(status_code=422, detail="Выберите доступный товар с ценой в RUB")
        subtotal = product.price_minor * payload.quantity
        promo = None
        promo_discount = 0
        code = normalize_promo_code(payload.promo_code)
        if code:
            promo = await session.scalar(select(PromoCode).where(PromoCode.code == code).with_for_update())
            if not promo or not is_promo_available(promo):
                raise HTTPException(status_code=422, detail="Промокод недействителен или его лимит исчерпан")
            promo_discount = subtotal * promo.discount_percent // 100
            promo.used_count += 1
        after_promo = subtotal - promo_discount
        bonus_used = min(customer.bonus_balance_minor, after_promo) if payload.use_bonus else 0
        customer.bonus_balance_minor -= bonus_used
        total = after_promo - bonus_used
        order = Order(
            public_number=str(uuid4()), user_id=customer.id,
            status="manual", payment_method="manual", subtotal_minor=subtotal,
            promo_code=promo.code if promo else None, promo_discount_minor=promo_discount,
            # A manual order is not a confirmed payment, so it never accrues
            # new bonuses automatically. It can still spend the customer's balance.
            bonus_used_minor=bonus_used, bonus_accrued_minor=0,
            total_minor=total, currency="RUB",
        )
        session.add(order)
        await session.flush()
        session.add(OrderItem(
            order_id=order.id, product_id=product.id, product_name_snapshot=product.name,
            configuration_snapshot={"source": "admin_manual"}, unit_price_minor=product.price_minor,
            quantity=payload.quantity, total_minor=total,
        ))
        await session.flush()
        result = {"id": order.id, "public_number": order.public_number, "subtotal_minor": order.subtotal_minor, "promo_discount_minor": order.promo_discount_minor, "bonus_used_minor": order.bonus_used_minor, "total_minor": order.total_minor, "currency": order.currency}
    await send_telegram(customer, f"<b>Создан ручной заказ {escape(result['public_number'])}</b>\n\n{escape(product.name)} × {payload.quantity}\nК оплате: <b>{result['total_minor'] / 100:.2f} RUB</b>\n\nОператор свяжется с вами по дальнейшим действиям.")
    return result


@app.post("/api/products/reprice")
async def reprice_products(payload: RepricePayload, _: Admin) -> dict:
    """Recalculate RUB prices from the stored supplier price: USD × rate × (1 + markup), rounded up to whole roubles."""
    factor = payload.rub_per_usd * (1 + payload.markup_percent / 100)
    async with SessionLocal() as session, session.begin():
        stmt = select(Product).where(Product.supplier_price_usd.is_not(None))
        if payload.product_type:
            stmt = stmt.where(Product.product_type == payload.product_type)
        if not payload.include_locked:
            stmt = stmt.where(Product.price_locked.is_(False))
        rows = list((await session.scalars(stmt)).all())
        for item in rows:
            item.price_minor = math.ceil(item.supplier_price_usd * factor) * 100
            item.currency = "RUB"
    return {"updated": len(rows)}


@app.get("/api/promocodes")
async def promo_codes(_: Admin) -> dict:
    async with SessionLocal() as session:
        rows = list((await session.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()))).all())
    return {"items": [promo_data(item) for item in rows]}


@app.post("/api/promocodes", status_code=status.HTTP_201_CREATED)
async def create_promo_code(payload: PromoCodePayload, _: Admin) -> dict:
    code = "".join(payload.code.upper().split())
    if not code or not all(char.isalnum() or char in "-_" for char in code):
        raise HTTPException(status_code=422, detail="Код: латинские буквы, цифры, «-» или «_»")
    async with SessionLocal() as session, session.begin():
        if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)):
            raise HTTPException(status_code=409, detail="Такой промокод уже существует")
        item = PromoCode(code=code, discount_percent=payload.discount_percent, usage_limit=payload.usage_limit)
        session.add(item)
        await session.flush()
        data = promo_data(item)
    return data


@app.post("/api/promocodes/{promo_id}/state")
async def set_promo_code_state(promo_id: int, payload: PromoCodeStatePayload, _: Admin) -> dict:
    async with SessionLocal() as session, session.begin():
        item = await session.get(PromoCode, promo_id)
        if not item:
            raise HTTPException(status_code=404, detail="Промокод не найден")
        item.active = payload.active
        await session.flush()
        data = promo_data(item)
    return data


@app.get("/api/tickets")
async def tickets(_: Admin, include_closed: bool = True, limit: int = 100) -> dict:
    limit = min(max(limit, 1), 200)
    async with SessionLocal() as session:
        stmt = select(SupportTicket).order_by(SupportTicket.updated_at.desc()).limit(limit)
        if not include_closed:
            stmt = stmt.where(SupportTicket.status == "open")
        rows = list((await session.scalars(stmt)).all())
        customers = {item.user_id: await session.get(User, item.user_id) for item in rows}
        counts = {
            ticket_id: count
            for ticket_id, count in (await session.execute(select(SupportMessage.ticket_id, func.count(SupportMessage.id)).where(SupportMessage.ticket_id.in_([item.id for item in rows])).group_by(SupportMessage.ticket_id))).all()
        } if rows else {}
    return {"items": [ticket_data(item, customers[item.user_id], counts.get(item.id, 0)) for item in rows]}


@app.get("/api/tickets/{ticket_id}")
async def ticket(ticket_id: int, _: Admin) -> dict:
    async with SessionLocal() as session:
        item = await session.scalar(select(SupportTicket).options(selectinload(SupportTicket.messages)).where(SupportTicket.id == ticket_id))
        if not item:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        customer = await session.get(User, item.user_id)
        data = ticket_data(item, customer)
        data["messages"] = [
            {"id": message.id, "sender_kind": message.sender_kind, "body": message.body, "created_at": as_iso(message.created_at)}
            for message in item.messages
        ]
    return data


@app.post("/api/tickets/{ticket_id}/reply")
async def reply(ticket_id: int, payload: ReplyPayload, _: Admin) -> dict:
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Введите текст ответа")
    async with SessionLocal() as session, session.begin():
        item = await session.get(SupportTicket, ticket_id)
        if not item:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        if item.status != "open":
            raise HTTPException(status_code=409, detail="Обращение закрыто")
        customer = await session.get(User, item.user_id)
        session.add(SupportMessage(ticket_id=item.id, sender_kind="admin_web", sender_telegram_id=None, body=body))
        item.updated_at = datetime.now(timezone.utc)
    if customer:
        await send_telegram(customer, f"<b>Ответ поддержки · {escape(item.public_number)}</b>\n\n{escape(body)}")
    return {"ok": True}


@app.post("/api/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: int, _: Admin) -> dict:
    async with SessionLocal() as session, session.begin():
        item = await session.get(SupportTicket, ticket_id)
        if not item:
            raise HTTPException(status_code=404, detail="Обращение не найдено")
        customer = await session.get(User, item.user_id)
        if item.status == "open":
            now = datetime.now(timezone.utc)
            item.status, item.closed_at, item.updated_at = "closed", now, now
    if customer:
        await send_telegram(customer, f"<b>Обращение {escape(item.public_number)} закрыто</b>\nЕсли вопрос останется, создайте новое обращение в разделе «Поддержка».")
    return {"ok": True}
