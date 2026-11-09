from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class CreatePaymentRequest(BaseModel):
    method: str = Field(default="khqr", pattern="^(khqr|cash|card|e_wallet)$")
    currency: str = Field(default="USD", pattern="^(USD|KHR)$")
    idempotency_key: str | None = Field(default=None, max_length=64)


class RefundPaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=255)
    external_refund_ref: str = Field(min_length=3, max_length=128)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    amount: Decimal
    currency: str = "USD"
    method: str
    status: str
    transaction_ref: str
    failure_reason: str | None = None
    qr_string: str | None = None
    qr_md5: str | None = None
    deeplink_url: str | None = None
    expires_at: datetime | None = None
    paid_at: datetime | None = None
    bakong_transaction_hash: str | None = None
    payer_account_masked: str | None = None
    attempt_count: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("expires_at", "paid_at", "created_at", "updated_at", when_used="json")
    def _serialize_utc_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
