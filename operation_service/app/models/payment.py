from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utc_now() -> datetime:
    """Return a timezone-naive UTC datetime consistent with Bakong service timestamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PaymentStatus:
    PENDING = "pending"
    PAID = "paid"
    SUCCESS = "success"  # Backward-compatible alias for paid
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUND_REQUIRED = "refund_required"
    REFUNDED = "refunded"

    ALL = (
        PENDING,
        PAID,
        SUCCESS,
        FAILED,
        EXPIRED,
        CANCELLED,
        REFUND_REQUIRED,
        REFUNDED,
    )

    TRANSITIONS: dict[str, set[str]] = {
        PENDING: {PAID, SUCCESS, FAILED, EXPIRED, CANCELLED},
        EXPIRED: {PENDING, PAID, SUCCESS, REFUND_REQUIRED, CANCELLED},
        FAILED: {PENDING, CANCELLED, REFUND_REQUIRED},
        PAID: {REFUNDED},
        SUCCESS: {REFUNDED},
        REFUND_REQUIRED: {REFUNDED},
        CANCELLED: set(),
        REFUNDED: set(),
    }

    @classmethod
    def can_transition(cls, current: str, target: str) -> bool:
        if current == target:
            return True
        return target in cls.TRANSITIONS.get(current, set())


class PaymentMethod:
    KHQR = "khqr"
    CASH = "cash"
    CARD = "card"
    E_WALLET = "e_wallet"

    ALL = (KHQR, CASH, CARD, E_WALLET)


class PaymentCurrency:
    USD = "USD"
    KHR = "KHR"

    ALL = (USD, KHR)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default=PaymentCurrency.USD)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=PaymentStatus.PENDING)
    # Gateway reference or transaction identifier.
    transaction_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Bakong KHQR specific fields
    qr_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_md5: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True, index=True)
    deeplink_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bakong_transaction_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    payer_account_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receiver_account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="payment")
    attempts: Mapped[list["PaymentAttempt"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[list["PaymentEvent"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )
    refunds: Mapped[list["Refund"]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", lazy="selectin"
    )


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (UniqueConstraint("payment_id", "attempt_number", name="uq_payment_attempt_number"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default=PaymentCurrency.USD)
    qr_string: Mapped[str] = mapped_column(Text, nullable=False)
    qr_md5: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    deeplink_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bill_number: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=PaymentStatus.PENDING)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bakong_transaction_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    payer_account_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_bakong_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)

    payment: Mapped["Payment"] = relationship(back_populates="attempts")


class PaymentEvent(Base):
    """Immutable append-only audit log for every payment lifecycle transition."""

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    qr_md5: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)

    payment: Mapped["Payment"] = relationship(back_populates="events")


class Refund(Base):
    """Manual / off-gateway Bakong refund record."""

    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default=PaymentCurrency.USD)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_manual")
    external_refund_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    processed_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)

    payment: Mapped["Payment"] = relationship(back_populates="refunds")


class IdempotencyRecord(Base):
    """PostgreSQL idempotency key store to prevent duplicate payment creation."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "user_id", "endpoint", name="uq_idempotency_key_user_endpoint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class VerificationJob(Base):
    """PostgreSQL FOR UPDATE SKIP LOCKED background verification job queue."""

    __tablename__ = "verification_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("payment_attempts.id", ondelete="CASCADE"), nullable=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    qr_md5: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now, nullable=False)
