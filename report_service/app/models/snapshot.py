from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserSnapshot(Base):
    """
    Local, denormalized copy of user_service's users - report_service_db
    owns this table outright. It is refreshed by SyncService pulling
    GET /user from user_service; it is never written to by anyone else.
    """

    __tablename__ = "users_snapshot"

    user_id: Mapped[int] = mapped_column(primary_key=True)  # same id as in user_service, not autoincrement here
    username: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ProductSnapshot(Base):
    """Local copy of operation_service's products, refreshed by SyncService."""

    __tablename__ = "products_snapshot"

    product_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    stock_quantity: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class OrderSnapshot(Base):
    """Local copy of operation_service's orders, refreshed by SyncService."""

    __tablename__ = "orders_snapshot"

    order_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    items: Mapped[list["OrderItemSnapshot"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class OrderItemSnapshot(Base):
    """Local copy of operation_service's order_items, refreshed by SyncService."""

    __tablename__ = "order_items_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders_snapshot.order_id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(255))
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2))
