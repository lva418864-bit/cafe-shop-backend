from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockMovementReason:
    RESTOCK = "restock"  # manual stock-in by staff
    SALE = "sale"  # decremented because an order was created
    CANCELLATION = "cancellation"  # restored because an order was cancelled
    ADJUSTMENT = "adjustment"  # manual correction (stock take, damage, etc.)

    ALL = (RESTOCK, SALE, CANCELLATION, ADJUSTMENT)


class StockMovement(Base):
    """Append-only ledger so every stock change is auditable."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)

    # Positive = stock added, negative = stock removed.
    change_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "order#12"

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
