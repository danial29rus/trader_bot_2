from __future__ import annotations

from datetime import datetime
from typing import Any

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    bonus_balance_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    type: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    image_url: Mapped[str | None] = mapped_column(Text)
    # Lower first; snapshot keeps popular brands in the order they were listed.
    sort_order: Mapped[int] = mapped_column(Integer, default=1000, server_default="1000")
    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("supplier", "product_type", "external_id", name="uq_product_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    supplier: Mapped[str] = mapped_column(String(40), default="demo")
    external_id: Mapped[str] = mapped_column(String(255))
    product_type: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    activation_type: Mapped[str | None] = mapped_column(String(100))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    supplier_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    # Set when an operator edits the price; imports then keep the manual price.
    price_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    available_by_import: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    category: Mapped[Category | None] = relationship(back_populates="products")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id", "configuration_key", name="uq_cart_line"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    configuration_key: Mapped[str] = mapped_column(String(64), default="")
    product: Mapped[Product] = relationship()


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending_payment")
    payment_method: Mapped[str] = mapped_column(String(30))
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    promo_code: Mapped[str | None] = mapped_column(String(40))
    promo_discount_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    bonus_used_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    bonus_accrued_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    total_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", cascade="all, delete-orphan")


class PromoCode(Base):
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    discount_percent: Mapped[int] = mapped_column(Integer)
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    product_name_snapshot: Mapped[str] = mapped_column(String(500))
    configuration_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    order: Mapped[Order] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    method: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    is_test: Mapped[bool] = mapped_column(Boolean, default=True)
    reference: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRun(Base):
    __tablename__ = "import_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    public_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    messages: Mapped[list[SupportMessage]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    sender_kind: Mapped[str] = mapped_column(String(20))
    sender_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")


class GameAccount(Base):
    """Recipient data a user entered for a game (player ID, server…) plus the last pack, for one-tap repeats."""
    __tablename__ = "game_accounts"
    __table_args__ = (UniqueConstraint("user_id", "category_id", name="uq_game_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Top-level brand category (the game), so one ID serves every region/variant of it.
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    last_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    category: Mapped[Category] = relationship()
    last_product: Mapped[Product | None] = relationship()
