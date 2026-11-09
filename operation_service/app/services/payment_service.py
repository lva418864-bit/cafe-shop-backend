from abc import ABC, abstractmethod
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exception import (
    OrderNotPayable,
    PaymentAlreadyExists,
    PaymentFailed,
    PaymentGatewayNotConfigured,
    PaymentNotFound,
)
from app.models.order import OrderStatus
from app.models.payment import (
    Payment,
    PaymentAttempt,
    PaymentCurrency,
    PaymentEvent,
    PaymentMethod,
    PaymentStatus,
    Refund,
    VerificationJob,
)
from app.repository import payment_repository as payment_repo
from app.schemas.payment_schema import CreatePaymentRequest, RefundPaymentRequest
from app.services.bakong_khqr import (
    BakongAmountValidationError,
    BakongClientInterface,
    BakongStateError,
    PaymentVerificationWorker,
    convert_usd_to_khr,
    get_bakong_client,
    utc_now,
)
from app.services.order_service import OrderService


class PaymentGateway(ABC):
    """Abstract interface for external payment gateway integration."""

    @abstractmethod
    def charge(self, amount: float, method: str) -> tuple[str, bool, str | None]:
        """Charge the specified amount. Returns (transaction_ref, succeeded, failure_reason)."""
        pass


class UnconfiguredPaymentGateway(PaymentGateway):
    """Default gateway implementation until an external payment provider SDK is integrated."""

    def charge(self, amount: float, method: str) -> tuple[str, bool, str | None]:
        raise PaymentGatewayNotConfigured()


class PaymentService:
    """
    Handles payment processing for customer orders using Bakong KHQR.

    - Legacy synchronous methods ('card', 'e_wallet') or unconfigured Bakong accounts
      raise HTTP 503 (PaymentGatewayNotConfigured) unless a custom PaymentGateway is injected.
    - When method='khqr' and BAKONG_ACCOUNT_ID is configured, generates a dynamic
      EMVCo KHQR code, stores its MD5 hash, and queues a PostgreSQL SKIP LOCKED
      verification job.
    """

    def __init__(
        self,
        gateway: PaymentGateway | None = None,
        bakong_client: BakongClientInterface | None = None,
        bakong_account_id: str | None = None,
    ):
        self.order_service = OrderService()
        self.gateway = gateway
        self._explicit_client = bakong_client
        self.bakong_account_id = bakong_account_id

    @property
    def bakong_client(self) -> BakongClientInterface:
        if self._explicit_client is not None:
            return self._explicit_client
        return get_bakong_client()

    @bakong_client.setter
    def bakong_client(self, client: BakongClientInterface | None):
        self._explicit_client = client

    @property
    def resolved_bakong_account(self) -> str | None:
        return self.bakong_account_id or settings.BAKONG_ACCOUNT_ID

    def pay_for_order(
        self,
        db: Session,
        order_id: int,
        request: CreatePaymentRequest,
        actor: str = "customer",
    ) -> Payment:
        # If Bakong KHQR is requested and a Bakong merchant account is configured,
        # use the asynchronous dynamic KHQR flow.
        if request.method == PaymentMethod.KHQR and self.resolved_bakong_account and self.gateway is None:
            return self.initiate_khqr_payment(db, order_id, request, actor=actor)

        order = self.order_service.get_order_by_id(db, order_id)

        if order.status != OrderStatus.PENDING:
            raise OrderNotPayable(order_id, order.status)

        existing = payment_repo.get_by_order_id(db, order_id)
        if existing and existing.status in (PaymentStatus.SUCCESS, PaymentStatus.PAID):
            raise PaymentAlreadyExists(order_id)

        active_gateway = self.gateway or UnconfiguredPaymentGateway()
        transaction_ref, succeeded, failure_reason = active_gateway.charge(order.total_amount, request.method)

        if existing:
            payment = existing
            payment.method = request.method
            payment.transaction_ref = transaction_ref
            payment.status = PaymentStatus.SUCCESS if succeeded else PaymentStatus.FAILED
            payment.failure_reason = None if succeeded else failure_reason
        else:
            payment = Payment(
                order_id=order_id,
                amount=order.total_amount,
                currency=request.currency,
                method=request.method,
                status=PaymentStatus.SUCCESS if succeeded else PaymentStatus.FAILED,
                transaction_ref=transaction_ref,
                failure_reason=None if succeeded else failure_reason,
            )

        if not succeeded:
            payment_repo.update(db, payment) if existing else payment_repo.create(db, payment)
            raise PaymentFailed(failure_reason or "Payment processing failed")

        payment = payment_repo.update(db, payment) if existing else payment_repo.create(db, payment)
        self.order_service.update_status(db, order_id, OrderStatus.PAID)
        return payment

    def initiate_khqr_payment(
        self,
        db: Session,
        order_id: int,
        request: CreatePaymentRequest,
        actor: str = "customer",
        force_regenerate: bool = False,
    ) -> Payment:
        account_id = self.resolved_bakong_account
        if not account_id:
            raise PaymentGatewayNotConfigured("Bakong KHQR merchant account (BAKONG_ACCOUNT_ID) is not configured.")

        order = self.order_service.get_order_by_id(db, order_id)
        if order.status != OrderStatus.PENDING:
            raise OrderNotPayable(order_id, order.status)

        existing = db.query(Payment).filter(Payment.order_id == order_id).with_for_update().first()
        if existing and existing.status in (PaymentStatus.PAID, PaymentStatus.SUCCESS):
            raise PaymentAlreadyExists(order_id)

        now = utc_now()
        # Idempotent return of active unexpired pending KHQR if currency matches and not forcing regeneration
        if (
            existing
            and not force_regenerate
            and existing.method == PaymentMethod.KHQR
            and existing.status == PaymentStatus.PENDING
            and existing.currency == request.currency.upper()
            and existing.expires_at
            and existing.expires_at > now + timedelta(seconds=10)
            and existing.qr_string
        ):
            return existing

        currency = request.currency.upper()
        if currency not in ("USD", "KHR"):
            raise BakongAmountValidationError(f"Unsupported payment currency: {request.currency}")

        if currency == PaymentCurrency.KHR:
            amount_dec = convert_usd_to_khr(order.total_amount)
            if amount_dec < Decimal("100"):
                raise BakongAmountValidationError("Minimum KHR payment is 100 KHR")
            max_khr = settings.BAKONG_MAX_AMOUNT_USD * Decimal(str(settings.BAKONG_USD_TO_KHR_RATE))
            if amount_dec > max_khr:
                raise BakongAmountValidationError(
                    f"Payment amount {amount_dec} KHR exceeds maximum allowed limit of {max_khr} KHR"
                )
        else:
            amount_dec = Decimal(str(order.total_amount)).quantize(Decimal("0.01"))
            if amount_dec < Decimal("0.01"):
                raise BakongAmountValidationError("Minimum USD payment is $0.01")
            if amount_dec > settings.BAKONG_MAX_AMOUNT_USD:
                raise BakongAmountValidationError(
                    f"Payment amount ${amount_dec} exceeds maximum allowed limit of ${settings.BAKONG_MAX_AMOUNT_USD}"
                )

        attempt_no = (existing.attempt_count + 1) if existing else 1
        bill_number = f"ORD-{order_id}-A{attempt_no}"
        expiry_minutes = getattr(settings, "PAYMENT_EXPIRY_MINUTES", 15)
        expires_at = now + timedelta(minutes=expiry_minutes)

        qr_string, qr_md5 = self.bakong_client.create_qr(
            amount=amount_dec,
            currency=currency,
            bill_number=bill_number,
            account_id=account_id,
            merchant_name=settings.BAKONG_MERCHANT_NAME,
            merchant_city=settings.BAKONG_MERCHANT_CITY,
            store_label=settings.BAKONG_STORE_LABEL,
            terminal_label=settings.BAKONG_TERMINAL_LABEL,
            phone_number=settings.BAKONG_PHONE_NUMBER,
        )

        order_status_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/orders/{order_id}"
        deeplink_url = self.bakong_client.generate_deeplink(qr_string, callback_url=order_status_url)

        if existing:
            # Mark any prior pending attempts/jobs as superseded
            for prior_attempt in existing.attempts:
                if prior_attempt.status == PaymentStatus.PENDING:
                    prior_attempt.status = PaymentStatus.EXPIRED
            db.query(VerificationJob).filter(
                VerificationJob.payment_id == existing.id,
                VerificationJob.status == "queued",
            ).update({"status": "completed"}, synchronize_session=False)

            prev_status = existing.status
            existing.amount = float(amount_dec)
            existing.currency = currency
            existing.method = PaymentMethod.KHQR
            existing.status = PaymentStatus.PENDING
            existing.transaction_ref = bill_number
            existing.failure_reason = None
            existing.qr_string = qr_string
            existing.qr_md5 = qr_md5
            existing.deeplink_url = deeplink_url
            existing.expires_at = expires_at
            existing.receiver_account = account_id
            existing.attempt_count = attempt_no
            payment = existing
        else:
            prev_status = None
            payment = Payment(
                order_id=order_id,
                amount=float(amount_dec),
                currency=currency,
                method=PaymentMethod.KHQR,
                status=PaymentStatus.PENDING,
                transaction_ref=bill_number,
                qr_string=qr_string,
                qr_md5=qr_md5,
                deeplink_url=deeplink_url,
                expires_at=expires_at,
                receiver_account=account_id,
                attempt_count=1,
            )
            db.add(payment)
            db.flush()

        attempt = PaymentAttempt(
            payment_id=payment.id,
            order_id=order_id,
            attempt_number=attempt_no,
            amount=float(amount_dec),
            currency=currency,
            qr_string=qr_string,
            qr_md5=qr_md5,
            deeplink_url=deeplink_url,
            bill_number=bill_number,
            status=PaymentStatus.PENDING,
            expires_at=expires_at,
        )
        db.add(attempt)
        db.flush()

        db.add(
            VerificationJob(
                payment_id=payment.id,
                attempt_id=attempt.id,
                order_id=order_id,
                qr_md5=qr_md5,
                status="queued",
                attempt_count=0,
                next_run_at=now,
            )
        )
        db.add(
            PaymentEvent(
                payment_id=payment.id,
                order_id=order_id,
                event_type="khqr.regenerated" if force_regenerate else "khqr.generated",
                previous_status=prev_status,
                new_status=PaymentStatus.PENDING,
                qr_md5=qr_md5,
                actor=actor,
            )
        )
        db.commit()
        db.refresh(payment)
        return payment

    def verify_order_payment(self, db: Session, order_id: int) -> Payment:
        payment = payment_repo.get_by_order_id(db, order_id)
        if not payment:
            raise PaymentNotFound(order_id)

        worker = PaymentVerificationWorker(
            bakong_client=self.bakong_client,
            expected_receiver_account=self.resolved_bakong_account,
            worker_id="api-verify",
        )
        return worker.verify_payment_now(db, payment)

    def cancel_order_payment(self, db: Session, order_id: int, actor: str = "customer") -> Payment:
        payment = db.query(Payment).filter(Payment.order_id == order_id).with_for_update().first()
        if not payment:
            raise PaymentNotFound(order_id)

        if not PaymentStatus.can_transition(payment.status, PaymentStatus.CANCELLED):
            raise BakongStateError(f"Cannot cancel payment in status '{payment.status}'")

        prev = payment.status
        payment.status = PaymentStatus.CANCELLED
        db.query(VerificationJob).filter(
            VerificationJob.payment_id == payment.id,
            VerificationJob.status == "queued",
        ).update({"status": "completed"}, synchronize_session=False)

        db.add(
            PaymentEvent(
                payment_id=payment.id,
                order_id=order_id,
                event_type="payment.cancelled",
                previous_status=prev,
                new_status=PaymentStatus.CANCELLED,
                qr_md5=payment.qr_md5,
                actor=actor,
            )
        )
        db.commit()
        db.refresh(payment)
        return payment

    def refund_order_payment(
        self,
        db: Session,
        order_id: int,
        request: RefundPaymentRequest,
        staff_user_id: int,
    ) -> Payment:
        payment = db.query(Payment).filter(Payment.order_id == order_id).with_for_update().first()
        if not payment:
            raise PaymentNotFound(order_id)

        if not PaymentStatus.can_transition(payment.status, PaymentStatus.REFUNDED):
            raise BakongStateError(f"Cannot refund payment in status '{payment.status}'")

        if Decimal(str(request.amount)) > Decimal(str(payment.amount)):
            raise BakongAmountValidationError("Refund amount cannot exceed the original payment amount")

        now = utc_now()
        prev = payment.status
        payment.status = PaymentStatus.REFUNDED
        db.add(
            Refund(
                payment_id=payment.id,
                order_id=order_id,
                amount=float(request.amount),
                currency=payment.currency,
                reason=request.reason,
                status="completed",
                external_refund_ref=request.external_refund_ref,
                processed_by_user_id=staff_user_id,
                refunded_at=now,
            )
        )
        db.add(
            PaymentEvent(
                payment_id=payment.id,
                order_id=order_id,
                event_type="payment.refunded",
                previous_status=prev,
                new_status=PaymentStatus.REFUNDED,
                qr_md5=payment.qr_md5,
                actor=f"staff:{staff_user_id}",
            )
        )
        db.commit()
        db.refresh(payment)
        return payment

    def get_payment_for_order(self, db: Session, order_id: int) -> Payment | None:
        payment = payment_repo.get_by_order_id(db, order_id)
        if (
            payment
            and payment.status == PaymentStatus.PENDING
            and payment.expires_at
            and utc_now() >= payment.expires_at
        ):
            payment.status = PaymentStatus.EXPIRED
            db.commit()
            db.refresh(payment)
        return payment
