from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class OrderStatus:
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    COMPLETED = "completed"

    ALL = (PENDING, PAID, CANCELLED, COMPLETED)

    # Allowed forward transitions. Anything not listed here is rejected.
    TRANSITIONS = {
        PENDING: {PAID, CANCELLED},
        PAID: {COMPLETED, CANCELLED},
        CANCELLED: set(),
        COMPLETED: set(),
    }


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # No FK constraint to user_service's `users` table on purpose: they are
    # separate databases owned by separate services. user_id is just the
    # "sub" claim copied from the caller's JWT.
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OrderStatus.PENDING)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    payment: Mapped["Payment | None"] = relationship(
        back_populates="order", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)

    product_name: Mapped[str] = mapped_column(String(255), nullable=False)  # snapshot at order time
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)  # snapshot at order time
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship("Product", lazy="selectin", viewonly=True)
    customizations: Mapped[list["OrderItemCustomization"]] = relationship(
        back_populates="order_item", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def product_image_url(self) -> str | None:
        return self.product.image_url if self.product else None

    @property
    def product_image_alt(self) -> str | None:
        return self.product.image_alt if self.product else self.product_name


class OrderItemCustomization(Base):
    __tablename__ = "order_item_customizations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )

    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_name: Mapped[str] = mapped_column(String(100), nullable=False)
    option_id: Mapped[int] = mapped_column(Integer, nullable=False)
    option_name: Mapped[str] = mapped_column(String(100), nullable=False)
    extra_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0.0)

    order_item: Mapped["OrderItem"] = relationship(back_populates="customizations")
