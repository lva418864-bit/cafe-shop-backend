from datetime import datetime
from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    username: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    # "customer" (default), "staff" or "admin". Encoded into the JWT so that
    # operation_service / report_service can authorize requests without
    # needing their own copy of the users table.
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="customer", server_default="customer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # NOTE: pass the callable itself (no parentheses) so it is re-evaluated
    # on every insert/update instead of being frozen once at import time.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
