from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.payment import Payment, PaymentStatus, VerificationJob
from app.models.order import Order, OrderStatus
from app.services.bakong_khqr import (
    BakongAmountValidationError,
    BakongAuthError,
    BakongApiTemporaryError,
    PaymentVerificationWorker,
    RealBakongClient,
    compute_poll_backoff_seconds,
    mask_secret,
    utc_now,
)


# =============================================================================
# 1. FAIL-FAST STARTUP VALIDATION TESTS
# =============================================================================


def test_startup_validation_fails_on_missing_token():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="local",
            BAKONG_TOKEN="",
            BAKONG_ACCOUNT_ID="merchant@bakong",
            DB_NAME="test_db",
            DB_USER="postgres",
            DB_PASSWORD="pw",
            JWT_SECRET_KEY="test_secret_key_32_characters_minimum!!",
        )
    assert "BAKONG_TOKEN is missing or empty" in str(exc_info.value)


def test_startup_validation_fails_on_missing_account_id():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="local",
            BAKONG_TOKEN="valid_dev_token",
            BAKONG_ACCOUNT_ID="",
            DB_NAME="test_db",
            DB_USER="postgres",
            DB_PASSWORD="pw",
            JWT_SECRET_KEY="test_secret_key_32_characters_minimum!!",
        )
    assert "BAKONG_ACCOUNT_ID is missing or empty" in str(exc_info.value)


def test_startup_validation_succeeds_with_valid_bakong_credentials():
    s = Settings(
        ENVIRONMENT="local",
        BAKONG_TOKEN="valid_dev_token",
        BAKONG_ACCOUNT_ID="merchant@bakong",
        DB_NAME="test_db",
        DB_USER="postgres",
        DB_PASSWORD="pw",
        JWT_SECRET_KEY="test_secret_key_32_characters_minimum!!",
    )
    assert s.BAKONG_ACCOUNT_ID == "merchant@bakong"


# =============================================================================
# 2. MASKING & BACKOFF SCHEDULE TESTS
# =============================================================================


def test_mask_secret_helper():
    assert mask_secret("merchant@bakong") == "...kong"
    assert mask_secret("9bca74e2d3198a2f") == "...8a2f"
    assert mask_secret("1234") == "****"
    assert mask_secret("12") == "****"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_backoff_schedule_progression():
    assert compute_poll_backoff_seconds(1) == 3
    assert compute_poll_backoff_seconds(10) == 3
    assert compute_poll_backoff_seconds(11) == 5
    assert compute_poll_backoff_seconds(20) == 5
    assert compute_poll_backoff_seconds(21) == 10
    assert compute_poll_backoff_seconds(30) == 10
    assert compute_poll_backoff_seconds(31) == 15
    assert compute_poll_backoff_seconds(100) == 15


# =============================================================================
# 3. REAL BAKONG CLIENT UNIT TESTS (MOCKED BAKONG-KHQR)
# =============================================================================


def test_real_bakong_client_create_qr_success():
    mock_sdk = MagicMock()
    mock_response = MagicMock()
    mock_response.__str__.return_value = "00020101021229220018coffee_shop@bakong52045814530384054040.505802KH5911Coffee Shop6010Phnom Penh62140110ORD-101-A16304ABCD"
    mock_response.md5 = "e3b0c44298fc1c149afbf4c8996fb924"
    mock_sdk.create_qr.return_value = mock_response

    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        max_amount_usd=1.00,
        khqr_sdk=mock_sdk,
    )

    qr_str, qr_md5 = client.create_qr(
        amount=Decimal("0.50"),
        currency="USD",
        bill_number="ORD-101-A1",
    )

    assert qr_str.startswith("00020101")
    assert qr_md5 == "e3b0c44298fc1c149afbf4c8996fb924"
    mock_sdk.create_qr.assert_called_once()


def test_real_bakong_client_amount_cap_enforcement_usd():
    mock_sdk = MagicMock()
    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        max_amount_usd=Decimal("1.00"),
        khqr_sdk=mock_sdk,
    )

    # Below cap: succeeds
    mock_sdk.create_qr.return_value = MagicMock(md5="abc12345", __str__=lambda self: "valid_qr")
    qr_str, qr_md5 = client.create_qr(amount=Decimal("0.99"), currency="USD", bill_number="ORD-1")
    assert qr_md5 == "abc12345"

    # Exceeds cap: raises BakongAmountValidationError
    with pytest.raises(BakongAmountValidationError) as exc:
        client.create_qr(amount=Decimal("1.01"), currency="USD", bill_number="ORD-2")
    assert "exceeds maximum allowed limit" in str(exc.value)

    # Below minimum (0.01): raises BakongAmountValidationError
    with pytest.raises(BakongAmountValidationError) as exc_min:
        client.create_qr(amount=Decimal("0.00"), currency="USD", bill_number="ORD-3")
    assert "greater than zero" in str(exc_min.value)


def test_real_bakong_client_amount_cap_enforcement_khr():
    mock_sdk = MagicMock()
    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        max_amount_usd=Decimal("1.00"),  # 1.00 * 4100 = 4100 KHR cap
        khqr_sdk=mock_sdk,
    )

    # 4,000 KHR is within limit
    mock_sdk.create_qr.return_value = MagicMock(md5="khr_md5", __str__=lambda self: "valid_khr_qr")
    _, qr_md5 = client.create_qr(amount=Decimal("4000"), currency="KHR", bill_number="ORD-KHR-1")
    assert qr_md5 == "khr_md5"

    # 4,200 KHR exceeds 4,100 KHR cap
    with pytest.raises(BakongAmountValidationError) as exc:
        client.create_qr(amount=Decimal("4200"), currency="KHR", bill_number="ORD-KHR-2")
    assert "exceeds maximum allowed limit" in str(exc.value)

    # Below minimum (100 KHR)
    with pytest.raises(BakongAmountValidationError) as exc_min:
        client.create_qr(amount=Decimal("50"), currency="KHR", bill_number="ORD-KHR-3")
    assert "Minimum KHR payment is 100 KHR" in str(exc_min.value)


def test_real_bakong_client_check_payment_paid():
    mock_sdk = MagicMock()
    mock_sdk.check_payment.return_value = "PAID"
    mock_sdk.get_payment.return_value = {
        "hash": "bakong_tx_hash_123456",
        "fromAccountId": "customer@aba",
        "toAccountId": "merchant@bakong",
        "currency": "USD",
        "amount": 0.50,
        "createdDateMs": 1762000000000,
        "acknowledgedDateMs": 1762000005000,
    }

    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        khqr_sdk=mock_sdk,
    )

    result = client.check_transaction_by_md5("e3b0c44298fc1c149afbf4c8996fb924")
    assert result.found is True
    assert result.response_code == 0
    assert result.tx_hash == "bakong_tx_hash_123456"
    assert result.amount == Decimal("0.50")
    assert result.currency == "USD"


def test_real_bakong_client_check_payment_unpaid():
    mock_sdk = MagicMock()
    mock_sdk.check_payment.return_value = "UNPAID"

    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        khqr_sdk=mock_sdk,
    )

    result = client.check_transaction_by_md5("e3b0c44298fc1c149afbf4c8996fb924")
    assert result.found is False
    assert result.response_code == 1
    assert result.message == "UNPAID"


def test_real_bakong_client_check_payment_invalid_or_expired_token():
    mock_sdk = MagicMock()
    mock_sdk.check_payment.side_effect = ValueError(
        "Your Developer Token is either incorrect or expired. Please renew it through Bakong Developer."
    )

    client = RealBakongClient(
        token="expired_token",
        account_id="merchant@bakong",
        khqr_sdk=mock_sdk,
    )

    with pytest.raises(BakongAuthError) as exc:
        client.check_transaction_by_md5("e3b0c44298fc1c149afbf4c8996fb924")
    assert "invalid or expired" in str(exc.value)


def test_real_bakong_client_check_payment_timeout_temporary_error():
    mock_sdk = MagicMock()
    mock_sdk.check_payment.side_effect = ValueError(
        "Bakong API took too long to respond. Please check transaction status later."
    )

    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        khqr_sdk=mock_sdk,
    )

    with pytest.raises(BakongApiTemporaryError) as exc:
        client.check_transaction_by_md5("e3b0c44298fc1c149afbf4c8996fb924")
    assert "temporary failure" in str(exc.value)


def test_real_bakong_client_check_bulk_payments():
    mock_sdk = MagicMock()
    mock_sdk.check_bulk_payments.return_value = ["md5_paid_1"]
    mock_sdk.get_payment.return_value = {
        "hash": "tx_bulk_1",
        "fromAccountId": "c1@aba",
        "toAccountId": "merchant@bakong",
        "currency": "USD",
        "amount": 0.75,
    }

    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        khqr_sdk=mock_sdk,
    )

    results = client.check_transaction_by_md5_list(["md5_paid_1", "md5_unpaid_2"])
    assert results["md5_paid_1"].found is True
    assert results["md5_paid_1"].tx_hash == "tx_bulk_1"
    assert results["md5_unpaid_2"].found is False
    assert results["md5_unpaid_2"].message == "UNPAID"


def test_real_bakong_client_generate_deeplink():
    mock_sdk = MagicMock()
    mock_sdk.generate_deeplink.return_value = "https://bakong.nbc.gov.kh/deeplink/sample123"

    client = RealBakongClient(
        token="test_token",
        account_id="merchant@bakong",
        khqr_sdk=mock_sdk,
    )

    deeplink = client.generate_deeplink("sample_qr_string", callback_url="http://localhost:3000/orders/42")
    assert deeplink == "https://bakong.nbc.gov.kh/deeplink/sample123"
    mock_sdk.generate_deeplink.assert_called_once()


# =============================================================================
# 4. WORKER RESILIENCE & CONCURRENCY TESTS
# =============================================================================


def test_worker_retries_on_bakong_auth_error_without_failing_payment(db_session, monkeypatch):
    """
    Edge Case: Token expired or invalid during worker polling.
    Worker marks the job retryable ('queued' with backoff), logs an alert,
    and leaves the payment status as PENDING (does not mark payment failed).
    """
    now = utc_now()
    order = Order(user_id=1, status=OrderStatus.PENDING, total_amount=0.50)
    db_session.add(order)
    db_session.flush()

    payment = Payment(
        order_id=order.id,
        amount=0.50,
        currency="USD",
        method="khqr",
        status=PaymentStatus.PENDING,
        transaction_ref="ORD-TEST-1",
        qr_string="test_qr_str",
        qr_md5="test_md5_retryable",
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(payment)
    db_session.flush()

    job = VerificationJob(
        payment_id=payment.id,
        order_id=order.id,
        qr_md5="test_md5_retryable",
        status="queued",
        attempt_count=0,
        next_run_at=now - timedelta(seconds=1),
    )
    db_session.add(job)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.check_transaction_by_md5.side_effect = BakongAuthError("Developer Token is expired.")

    worker = PaymentVerificationWorker(bakong_client=mock_client)
    processed = worker.process_due_jobs(db_session)

    assert processed == 1
    db_session.refresh(payment)
    db_session.refresh(job)

    # Payment must REMAIN pending; job must be scheduled for retry with backoff
    assert payment.status == PaymentStatus.PENDING
    assert job.status == "queued"
    assert job.attempt_count == 1
    assert "Developer Token is expired" in (job.last_error or "")
    assert job.next_run_at > now


def test_worker_idempotent_duplicate_paid_result(db_session):
    """
    Edge Case: Worker receives a second PAID result for an already PAID payment.
    Must not double-credit or fail, and must maintain idempotency.
    """
    now = utc_now()
    order = Order(user_id=1, status=OrderStatus.PAID, total_amount=0.50)
    db_session.add(order)
    db_session.flush()

    payment = Payment(
        order_id=order.id,
        amount=0.50,
        currency="USD",
        method="khqr",
        status=PaymentStatus.PAID,
        transaction_ref="ORD-PAID-1",
        qr_string="test_qr_paid",
        qr_md5="test_md5_paid",
        bakong_transaction_hash="tx_orig_hash_123",
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(payment)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.check_transaction_by_md5.return_value = MagicMock(
        found=True,
        tx_hash="tx_orig_hash_123",
        amount=Decimal("0.50"),
        currency="USD",
        to_account_id="coffee_shop@bakong",
    )

    worker = PaymentVerificationWorker(bakong_client=mock_client)
    res = worker.verify_payment_now(db_session, payment)

    assert res.status == PaymentStatus.PAID
    assert order.status == OrderStatus.PAID


def test_worker_concurrency_skip_locked(db_session):
    """
    Tests that FOR UPDATE SKIP LOCKED prevents two workers from processing the same job.
    """
    now = utc_now()
    order = Order(user_id=1, status=OrderStatus.PENDING, total_amount=0.50)
    db_session.add(order)
    db_session.flush()

    payment = Payment(
        order_id=order.id,
        amount=0.50,
        currency="USD",
        method="khqr",
        status=PaymentStatus.PENDING,
        transaction_ref="ORD-CONCURRENCY-1",
        qr_string="test_qr_concurrency",
        qr_md5="test_md5_concurrency",
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(payment)
    db_session.flush()

    job = VerificationJob(
        payment_id=payment.id,
        order_id=order.id,
        qr_md5="test_md5_concurrency",
        status="queued",
        attempt_count=0,
        next_run_at=now - timedelta(seconds=1),
    )
    db_session.add(job)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.check_transaction_by_md5.return_value = MagicMock(found=False, response_code=1, message="UNPAID")

    worker_1 = PaymentVerificationWorker(bakong_client=mock_client, worker_id="w1")
    count = worker_1.process_due_jobs(db_session)
    assert count == 1
    db_session.refresh(job)
    assert job.attempt_count == 1


def test_payment_response_serializes_utc_z_suffix(db_session):
    """
    Regression test: PaymentResponse JSON serialization must emit ISO-8601 UTC
    timestamps with a trailing 'Z' so browsers in non-UTC timezones (+07:00) do
    not misparse naive UTC strings as local time.
    """
    from app.schemas.payment_schema import PaymentResponse

    now = utc_now()
    resp = PaymentResponse(
        id=1,
        order_id=10,
        amount=Decimal("0.50"),
        currency="USD",
        method="khqr",
        status=PaymentStatus.PENDING,
        transaction_ref="ORD-UTC-1",
        expires_at=now + timedelta(minutes=15),
        created_at=now,
        updated_at=now,
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["expires_at"].endswith("Z")
    assert dumped["created_at"].endswith("Z")
    assert dumped["updated_at"].endswith("Z")
