from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from httpx import Response

from app.core.config import settings
from app.models.category import Category
from app.models.order import Order, OrderStatus
from app.models.payment import (
    Payment,
    PaymentEvent,
    PaymentStatus,
    Refund,
    VerificationJob,
)
from app.models.product import Product
from app.services.bakong_khqr import (
    BakongAmountValidationError,
    BakongConfigurationError,
    BakongOpenAPIClient,
    PaymentVerificationWorker,
    compute_crc16_ccitt,
    compute_poll_backoff_seconds,
    compute_qr_md5,
    convert_usd_to_khr,
    format_khqr_amount,
    generate_khqr_string,
    mask_bakong_account,
    parse_khqr_tlv,
    utc_now,
    verify_khqr_crc,
)
from coffee_testing import TEST_NAME_PREFIX, auth_header

BAKONG_TEST_BASE_URL = "https://sit-api-bakong.nbc.gov.kh"
MERCHANT_BAKONG_ID = "coffeeshop@aclb"


# ============================================================================
# 1. UNIT TESTS: KHQR EMVCo TLV, CRC16-CCITT, MD5, Currency & State Machine
# ============================================================================


def test_khqr_generation_and_crc16_verification():
    qr = generate_khqr_string(
        bank_account=MERCHANT_BAKONG_ID,
        merchant_name="Coffee Shop Phnom Penh",
        merchant_city="Phnom Penh",
        amount=Decimal("4.50"),
        currency="USD",
        bill_number="ORD-42-A1",
        store_label="MainBranch",
        terminal_label="POS-01",
        phone_number="85512345678",
        created_at_ms=1762000000000,
        expires_at_ms=1762000180000,
    )

    assert qr.startswith("000201010212")
    assert qr[-4:] == compute_crc16_ccitt(qr[:-4])
    assert verify_khqr_crc(qr) is True

    # Tampering with 1 character must invalidate the CRC16-CCITT checksum
    tampered = qr[:-5] + ("9" if qr[-5] != "9" else "8") + qr[-4:]
    assert verify_khqr_crc(tampered) is False

    tags = parse_khqr_tlv(qr)
    assert tags["00"] == "01"
    assert tags["01"] == "12"  # Dynamic QR
    assert MERCHANT_BAKONG_ID in tags["29"]
    assert tags["52"] == "5814"
    assert tags["53"] == "840"  # USD
    assert tags["54"] == "4.50"
    assert tags["58"] == "KH"
    assert tags["59"] == "Coffee Shop Phnom Penh"
    assert tags["60"] == "Phnom Penh"
    assert "ORD-42-A1" in tags["62"]
    assert "1762000180000" in tags["99"]

    md5_hash = compute_qr_md5(qr)
    assert len(md5_hash) == 32
    assert all(c in "0123456789abcdef" for c in md5_hash)


def test_khqr_corporate_merchant_tag_30():
    qr = generate_khqr_string(
        bank_account=MERCHANT_BAKONG_ID,
        merchant_name="Coffee Shop Corp",
        merchant_city="Phnom Penh",
        amount=Decimal("18000"),
        currency="KHR",
        bill_number="ORD-99-A1",
        merchant_id="MID123456",
        acquiring_bank="ACLEDA Bank Plc.",
        created_at_ms=1762000000000,
        expires_at_ms=1762000180000,
    )
    assert verify_khqr_crc(qr) is True
    tags = parse_khqr_tlv(qr)
    assert "30" in tags
    assert "MID123456" in tags["30"]
    assert tags["53"] == "116"  # KHR
    assert tags["54"] == "18000"


def test_khqr_amount_and_currency_validation():
    assert format_khqr_amount(Decimal("3.5"), "USD") == "3.50"
    assert format_khqr_amount(16400, "KHR") == "16400"
    assert convert_usd_to_khr(Decimal("4.00"), rate=4100) == Decimal("16400")

    with pytest.raises(BakongAmountValidationError):
        format_khqr_amount(Decimal("0"), "USD")

    with pytest.raises(BakongAmountValidationError):
        format_khqr_amount(Decimal("-5.00"), "USD")

    with pytest.raises(BakongAmountValidationError):
        format_khqr_amount(Decimal("18000.50"), "KHR")  # KHR cannot have decimals

    with pytest.raises(BakongAmountValidationError):
        format_khqr_amount(Decimal("4.559"), "USD")  # >2 decimal places

    with pytest.raises(BakongAmountValidationError):
        format_khqr_amount(Decimal("10.00"), "EUR")  # Unsupported currency

    with pytest.raises(BakongConfigurationError):
        generate_khqr_string(
            bank_account="invalid_account_without_at_sign",
            merchant_name="Shop",
            merchant_city="Phnom Penh",
            amount=Decimal("1.00"),
            currency="USD",
            bill_number="ORD-1",
        )


def test_payment_state_machine_and_account_masking():
    assert PaymentStatus.can_transition(PaymentStatus.PENDING, PaymentStatus.PAID) is True
    assert PaymentStatus.can_transition(PaymentStatus.PENDING, PaymentStatus.EXPIRED) is True
    assert PaymentStatus.can_transition(PaymentStatus.PENDING, PaymentStatus.CANCELLED) is True
    assert PaymentStatus.can_transition(PaymentStatus.EXPIRED, PaymentStatus.PAID) is True
    assert PaymentStatus.can_transition(PaymentStatus.EXPIRED, PaymentStatus.REFUND_REQUIRED) is True
    assert PaymentStatus.can_transition(PaymentStatus.PAID, PaymentStatus.REFUNDED) is True
    assert PaymentStatus.can_transition(PaymentStatus.PAID, PaymentStatus.PENDING) is False
    assert PaymentStatus.can_transition(PaymentStatus.CANCELLED, PaymentStatus.PAID) is False
    assert PaymentStatus.can_transition(PaymentStatus.REFUNDED, PaymentStatus.PAID) is False

    assert mask_bakong_account("sokha_chan@aba") == "so***an@aba"
    assert mask_bakong_account("abc@wing") == "a***@wing"
    assert mask_bakong_account(None) is None


def test_poll_backoff_schedule():
    assert compute_poll_backoff_seconds(1) == 3
    assert compute_poll_backoff_seconds(10) == 3
    assert compute_poll_backoff_seconds(15) == 5
    assert compute_poll_backoff_seconds(25) == 10


# ============================================================================
# 2. INTEGRATION & WORKER TESTS (with respx Bakong Open API Mocks)
# ============================================================================


def _seed_pending_order(db_session, price: Decimal = Decimal("4.50"), qty: int = 1) -> int:
    cat = Category(name=f"{TEST_NAME_PREFIX}KHQRCat-{datetime.now().timestamp()}", description="KHQR Test")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}Iced Latte-{datetime.now().timestamp()}",
        description="Cambodian iced latte",
        price=price,
        stock_quantity=50,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()

    order = Order(
        user_id=101,
        status=OrderStatus.PENDING,
        total_amount=float(price * qty),
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order.id


def _use_httpx_bakong_client(monkeypatch, max_amount_usd: Decimal = Decimal("50.00")):
    """Configure test-scoped BakongOpenAPIClient (use_httpx=True) so respx mocks intercept HTTP calls."""
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", MERCHANT_BAKONG_ID)
    monkeypatch.setattr(settings, "BAKONG_API_BASE_URL", BAKONG_TEST_BASE_URL)
    monkeypatch.setattr(settings, "BAKONG_MAX_AMOUNT_USD", max_amount_usd)
    monkeypatch.setattr(
        "app.services.payment_service.get_bakong_client",
        lambda: BakongOpenAPIClient(
            base_url=BAKONG_TEST_BASE_URL,
            account_id=MERCHANT_BAKONG_ID,
            max_amount_usd=max_amount_usd,
            use_httpx=True,
        ),
    )


def test_khqr_payment_creation_idempotency_and_verification_success(client, db_session, monkeypatch):
    _use_httpx_bakong_client(monkeypatch)

    order_id = _seed_pending_order(db_session, price=Decimal("4.50"), qty=2)  # Total = 9.00 USD
    customer_headers = auth_header(sub=101, role="customer")

    # 1. Initiate Bakong KHQR payment
    res1 = client.post(
        f"/order/{order_id}/payment",
        headers=customer_headers,
        json={"method": "khqr", "currency": "USD"},
    )
    assert res1.status_code == 200
    p1 = res1.json()["data"]
    assert p1["status"] == "pending"
    assert p1["method"] == "khqr"
    assert p1["currency"] == "USD"
    assert Decimal(p1["amount"]) == Decimal("9.00")
    assert p1["qr_string"] is not None
    assert verify_khqr_crc(p1["qr_string"]) is True
    assert p1["qr_md5"] == compute_qr_md5(p1["qr_string"])

    # 2. Idempotent repeat call returns the exact same active KHQR and MD5
    res2 = client.post(
        f"/order/{order_id}/payment",
        headers=customer_headers,
        json={"method": "khqr", "currency": "USD"},
    )
    assert res2.status_code == 200
    p2 = res2.json()["data"]
    assert p2["id"] == p1["id"]
    assert p2["qr_md5"] == p1["qr_md5"]
    assert p2["attempt_count"] == 1

    # 3. Verify via Bakong Open API when customer has paid
    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5").mock(
            return_value=Response(
                200,
                json={
                    "responseCode": 0,
                    "responseMessage": "Getting transaction successfully.",
                    "errorCode": None,
                    "data": {
                        "hash": "bakong_tx_hash_9001",
                        "fromAccountId": "customer_chan@aba",
                        "toAccountId": MERCHANT_BAKONG_ID,
                        "currency": "USD",
                        "amount": 9.0,
                        "description": f"ORD-{order_id}-A1",
                        "createdDateMs": 1762000010000,
                        "acknowledgedDateMs": 1762000012000,
                    },
                },
            )
        )

        verify_res = client.post(f"/order/{order_id}/payment/verify", headers=customer_headers)
        assert verify_res.status_code == 200
        verified = verify_res.json()["data"]
        assert verified["status"] == "paid"
        assert verified["bakong_transaction_hash"] == "bakong_tx_hash_9001"
        assert verified["payer_account_masked"] == "cu***an@aba"

    # Order status must now be 'paid'
    order_res = client.get(f"/order/{order_id}", headers=customer_headers)
    assert order_res.json()["data"]["status"] == "paid"


def test_worker_handles_expired_qr_and_regeneration(client, db_session, monkeypatch):
    _use_httpx_bakong_client(monkeypatch)

    order_id = _seed_pending_order(db_session, price=Decimal("3.25"), qty=1)
    customer_headers = auth_header(sub=101, role="customer")

    res = client.post(
        f"/order/{order_id}/payment",
        headers=customer_headers,
        json={"method": "khqr", "currency": "USD"},
    )
    first_md5 = res.json()["data"]["qr_md5"]

    # Force expires_at into the past to simulate QR expiration
    payment = db_session.query(Payment).filter(Payment.order_id == order_id).first()
    payment.expires_at = utc_now() - timedelta(seconds=10)
    db_session.commit()

    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5").mock(
            return_value=Response(
                200,
                json={
                    "responseCode": 1,
                    "errorCode": 1,
                    "responseMessage": "Transaction could not be found. Please check and try again.",
                    "data": None,
                },
            )
        )

        worker = PaymentVerificationWorker(
            bakong_client=BakongOpenAPIClient(base_url=BAKONG_TEST_BASE_URL, use_httpx=True),
            expected_receiver_account=MERCHANT_BAKONG_ID,
        )
        processed = worker.process_due_jobs(db_session)
        assert processed == 1

    db_session.refresh(payment)
    assert payment.status == PaymentStatus.EXPIRED

    # Regenerate KHQR creates Attempt #2 with a brand-new MD5 and resets status to 'pending'
    regen_res = client.post(
        f"/order/{order_id}/payment/regenerate",
        headers=customer_headers,
        json={"method": "khqr", "currency": "KHR"},
    )
    assert regen_res.status_code == 200
    regen_data = regen_res.json()["data"]
    assert regen_data["status"] == "pending"
    assert regen_data["currency"] == "KHR"
    assert regen_data["attempt_count"] == 2
    assert regen_data["qr_md5"] != first_md5


def test_worker_handles_bakong_timeout_and_token_auto_renewal(db_session, monkeypatch):
    _use_httpx_bakong_client(monkeypatch)
    monkeypatch.setattr(settings, "BAKONG_REGISTERED_EMAIL", "merchant@coffeeshop.kh")

    order_id = _seed_pending_order(db_session, price=Decimal("5.00"), qty=1)
    from app.schemas.payment_schema import CreatePaymentRequest
    from app.services.payment_service import PaymentService

    svc = PaymentService(bakong_account_id=MERCHANT_BAKONG_ID)
    payment = svc.initiate_khqr_payment(db_session, order_id, CreatePaymentRequest(method="khqr", currency="USD"))

    # Case A: Bakong API network timeout -> job remains queued with incremented attempt_count
    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5").mock(side_effect=httpx.ReadTimeout("Bakong gateway timeout"))

        worker = PaymentVerificationWorker(
            bakong_client=BakongOpenAPIClient(
                base_url=BAKONG_TEST_BASE_URL,
                token="expired-jwt",
                registered_email="merchant@coffeeshop.kh",
                use_httpx=True,
            ),
            expected_receiver_account=MERCHANT_BAKONG_ID,
        )
        worker.process_due_jobs(db_session)

    job = db_session.query(VerificationJob).filter(VerificationJob.payment_id == payment.id).first()
    assert job.status == "queued"
    assert job.attempt_count == 1
    assert "timeout" in (job.last_error or "").lower()

    # Reset next_run_at so job is due immediately for Case B
    job.next_run_at = utc_now() - timedelta(seconds=1)
    db_session.commit()

    # Case B: Bakong returns errorCode=6 (token expired) -> auto-calls /v1/renew_token then succeeds!
    call_counter = {"md5_calls": 0}

    def md5_handler(request):
        call_counter["md5_calls"] += 1
        if call_counter["md5_calls"] == 1:
            return Response(
                200,
                json={"responseCode": 1, "errorCode": 6, "responseMessage": "Token has expired", "data": None},
            )
        return Response(
            200,
            json={
                "responseCode": 0,
                "responseMessage": "Success",
                "errorCode": None,
                "data": {
                    "hash": "tx_after_token_renewal_01",
                    "fromAccountId": "dara@wing",
                    "toAccountId": MERCHANT_BAKONG_ID,
                    "currency": "USD",
                    "amount": 5.0,
                },
            },
        )

    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/renew_token").mock(
            return_value=Response(
                200,
                json={"responseCode": 0, "data": {"token": "freshly-renewed-bakong-jwt"}},
            )
        )
        rmock.post("/v1/check_transaction_by_md5").mock(side_effect=md5_handler)

        worker.process_due_jobs(db_session)

    db_session.refresh(payment)
    assert payment.status == PaymentStatus.PAID
    assert payment.bakong_transaction_hash == "tx_after_token_renewal_01"
    assert worker.bakong_client.token == "freshly-renewed-bakong-jwt"


def test_underpayment_overpayment_and_late_payment_reconciliation(db_session, monkeypatch):
    _use_httpx_bakong_client(monkeypatch)
    from app.schemas.payment_schema import CreatePaymentRequest
    from app.services.payment_service import PaymentService

    svc = PaymentService(bakong_account_id=MERCHANT_BAKONG_ID)

    # 1. Underpayment ($3.00 paid for a $6.00 order) -> status='failed' + pending_manual Refund created
    order_under = _seed_pending_order(db_session, price=Decimal("6.00"), qty=1)
    pay_under = svc.initiate_khqr_payment(db_session, order_under, CreatePaymentRequest(method="khqr", currency="USD"))

    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5").mock(
            return_value=Response(
                200,
                json={
                    "responseCode": 0,
                    "data": {
                        "hash": "tx_underpaid_001",
                        "fromAccountId": "buyer@aba",
                        "toAccountId": MERCHANT_BAKONG_ID,
                        "currency": "USD",
                        "amount": 3.00,
                    },
                },
            )
        )
        worker = PaymentVerificationWorker(
            bakong_client=BakongOpenAPIClient(base_url=BAKONG_TEST_BASE_URL, use_httpx=True),
            expected_receiver_account=MERCHANT_BAKONG_ID,
        )
        worker.verify_payment_now(db_session, pay_under)

    assert pay_under.status == PaymentStatus.FAILED
    assert "Underpaid" in (pay_under.failure_reason or "")
    under_refund = db_session.query(Refund).filter(Refund.payment_id == pay_under.id).first()
    assert under_refund is not None
    assert Decimal(str(under_refund.amount)) == Decimal("3.00")

    # 2. Late payment after order was already cancelled -> status='refund_required'
    order_late = _seed_pending_order(db_session, price=Decimal("4.00"), qty=1)
    pay_late = svc.initiate_khqr_payment(db_session, order_late, CreatePaymentRequest(method="khqr", currency="USD"))
    pay_late.status = PaymentStatus.EXPIRED
    order_obj = db_session.query(Order).filter(Order.id == order_late).first()
    order_obj.status = OrderStatus.CANCELLED
    db_session.commit()

    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5").mock(
            return_value=Response(
                200,
                json={
                    "responseCode": 0,
                    "data": {
                        "hash": "tx_late_on_cancelled_order",
                        "fromAccountId": "late_payer@aclb",
                        "toAccountId": MERCHANT_BAKONG_ID,
                        "currency": "USD",
                        "amount": 4.00,
                    },
                },
            )
        )
        recovered = worker.reconcile_expired_payments(db_session)
        assert recovered == 1

    db_session.refresh(pay_late)
    assert pay_late.status == PaymentStatus.REFUND_REQUIRED
    late_refund = db_session.query(Refund).filter(Refund.payment_id == pay_late.id).first()
    assert late_refund is not None
    assert late_refund.reason == "late_payment_on_cancelled_order"


def test_staff_manual_refund_and_bulk_md5_list(client, db_session, monkeypatch):
    _use_httpx_bakong_client(monkeypatch)
    order_id = _seed_pending_order(db_session, price=Decimal("5.50"), qty=1)
    customer_headers = auth_header(sub=101, role="customer")
    staff_headers = auth_header(sub=2, role="staff")

    client.post(f"/order/{order_id}/payment", headers=customer_headers, json={"method": "khqr", "currency": "USD"})

    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5").mock(
            return_value=Response(
                200,
                json={
                    "responseCode": 0,
                    "data": {
                        "hash": "tx_to_refund_100",
                        "fromAccountId": "customer@aba",
                        "toAccountId": MERCHANT_BAKONG_ID,
                        "currency": "USD",
                        "amount": 5.50,
                    },
                },
            )
        )
        client.post(f"/order/{order_id}/payment/verify", headers=customer_headers)

    # Staff records manual Bakong refund
    refund_res = client.post(
        f"/order/{order_id}/payment/refund",
        headers=staff_headers,
        json={
            "amount": "5.50",
            "reason": "Out of oat milk, refunded via Bakong app",
            "external_refund_ref": "BAKONG-REF-20261102-001",
        },
    )
    assert refund_res.status_code == 200
    assert refund_res.json()["data"]["status"] == "refunded"

    events = db_session.query(PaymentEvent).filter(PaymentEvent.order_id == order_id).all()
    event_types = [e.event_type for e in events]
    assert "khqr.generated" in event_types
    assert "payment.paid" in event_types
    assert "payment.refunded" in event_types

    # Also verify check_transaction_by_md5_list batch client parsing
    api_client = BakongOpenAPIClient(base_url=BAKONG_TEST_BASE_URL, token="jwt-token", use_httpx=True)
    with respx.mock(base_url=BAKONG_TEST_BASE_URL) as rmock:
        rmock.post("/v1/check_transaction_by_md5_list").mock(
            return_value=Response(
                200,
                json={
                    "responseCode": 0,
                    "data": [
                        {
                            "md5": "md5_paid_1",
                            "status": "SUCCESS",
                            "data": {
                                "hash": "batch_hash_1",
                                "fromAccountId": "p1@aba",
                                "toAccountId": MERCHANT_BAKONG_ID,
                                "currency": "USD",
                                "amount": 2.5,
                            },
                        },
                        {"md5": "md5_unpaid_2", "status": "NOT_FOUND", "data": None},
                    ],
                },
            )
        )
        batch_out = api_client.check_transaction_by_md5_list(["md5_paid_1", "md5_unpaid_2"])
        assert batch_out["md5_paid_1"].found is True
        assert batch_out["md5_paid_1"].tx_hash == "batch_hash_1"
        assert batch_out["md5_unpaid_2"].found is False


def test_mock_pay_endpoint_is_removed_from_runtime(client):
    """Verifies that /payment/mock-pay/{md5} and runtime MockBakongClient do not exist in runtime."""
    import app.services.bakong_khqr as bakong_module

    assert not hasattr(bakong_module, "MockBakongClient")
    res = client.post("/payment/mock-pay/a1b2c3d4e5f678901234567890abcdef")
    assert res.status_code == 404
