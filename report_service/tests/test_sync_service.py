from datetime import datetime
from decimal import Decimal
import jwt
import pytest
import respx
from httpx import Response

from app.core.config import settings
from app.core.exception import SyncFailed
from app.models.snapshot import OrderSnapshot, ProductSnapshot, UserSnapshot
from app.services.sync_service import SyncService
from coffee_testing import (
    TEST_JWT_SECRET,
    api_page,
    order_payload,
    product_payload,
    user_payload,
)


@respx.mock
def test_sync_upserts_and_purges_stale(db_session):
    sync_svc = SyncService()

    # Initial upstream data: 2 users, 2 products, 2 orders
    users_page_1 = api_page([user_payload(1), user_payload(2)])
    products_page_1 = api_page([product_payload(10), product_payload(20)])
    orders_page_1 = api_page([order_payload(101, 1), order_payload(102, 2)])

    u_route = respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(return_value=Response(200, json=users_page_1))
    p_route = respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/product").mock(
        return_value=Response(200, json=products_page_1)
    )
    o_route = respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/order").mock(
        return_value=Response(200, json=orders_page_1)
    )

    # Initial sync
    res1 = sync_svc.sync_all(db_session)
    assert res1["users_synced"] == 2
    assert res1["products_synced"] == 2
    assert res1["orders_synced"] == 2
    assert db_session.query(UserSnapshot).count() == 2
    assert db_session.query(ProductSnapshot).count() == 2
    assert db_session.query(OrderSnapshot).count() == 2

    # Now simulate upstream deletion: User 2, Product 20, Order 102 were deleted upstream
    users_page_2 = api_page([user_payload(1)])  # User 2 gone
    products_page_2 = api_page([product_payload(10)])  # Product 20 gone
    orders_page_2 = api_page([order_payload(101, 1)])  # Order 102 gone

    u_route.return_value = Response(200, json=users_page_2)
    p_route.return_value = Response(200, json=products_page_2)
    o_route.return_value = Response(200, json=orders_page_2)

    # Subsequent sync purges stale records from snapshots
    res2 = sync_svc.sync_all(db_session)
    assert res2["stale_users_removed"] == 1
    assert res2["stale_products_removed"] == 1
    assert res2["stale_orders_removed"] == 1

    # Verified clean: only active upstream items remain
    assert db_session.query(UserSnapshot).count() == 1
    assert db_session.get(UserSnapshot, 1) is not None
    assert db_session.get(UserSnapshot, 2) is None

    assert db_session.query(ProductSnapshot).count() == 1
    assert db_session.get(ProductSnapshot, 10) is not None
    assert db_session.get(ProductSnapshot, 20) is None

    assert db_session.query(OrderSnapshot).count() == 1
    assert db_session.get(OrderSnapshot, 101) is not None
    assert db_session.get(OrderSnapshot, 102) is None


@respx.mock
def test_user_service_pagination_and_mapping(db_session):
    sync_svc = SyncService()
    raw_user = user_payload(10, role="staff", is_active=True, created_at="2026-02-01T12:00:00")
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(return_value=Response(200, json=api_page([raw_user])))

    synced, stale = sync_svc.sync_users(db_session)
    assert synced == 1
    assert stale == 0

    user = db_session.get(UserSnapshot, 10)
    assert user is not None
    assert user.username == raw_user["username"]
    assert user.email == raw_user["email"]
    assert user.role == "staff"
    assert user.is_active is True
    assert user.created_at == datetime(2026, 2, 1, 12, 0, 0)
    assert user.synced_at is not None


@respx.mock
def test_product_service_pagination_and_mapping(db_session):
    sync_svc = SyncService()
    raw_product = product_payload(42, price="5.50", stock_quantity=15, is_active=True)
    respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/product").mock(
        return_value=Response(200, json=api_page([raw_product]))
    )

    synced, stale = sync_svc.sync_products(db_session)
    assert synced == 1
    assert stale == 0

    prod = db_session.get(ProductSnapshot, 42)
    assert prod is not None
    assert prod.name == raw_product["name"]
    assert Decimal(str(prod.price)) == Decimal("5.50")
    assert prod.stock_quantity == 15
    assert prod.is_active is True


@respx.mock
def test_order_service_pagination_and_nested_items(db_session):
    sync_svc = SyncService()
    raw_order = order_payload(888, 10, status="completed", items=[(1, 2, "3.50"), (2, 1, "4.00")])
    respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/order").mock(
        return_value=Response(200, json=api_page([raw_order]))
    )

    synced, stale = sync_svc.sync_orders(db_session)
    assert synced == 1
    assert stale == 0

    order = db_session.get(OrderSnapshot, 888)
    assert order is not None
    assert order.user_id == 10
    assert order.status == "completed"
    assert Decimal(str(order.total_amount)) == Decimal("11.00")
    assert len(order.items) == 2
    item1 = order.items[0]
    assert item1.product_id == 1
    assert item1.quantity == 2
    assert Decimal(str(item1.unit_price)) == Decimal("3.50")
    assert Decimal(str(item1.subtotal)) == Decimal("7.00")


@respx.mock
def test_multiple_pages_pagination_and_termination(db_session):
    sync_svc = SyncService()

    page1 = {
        "success": True,
        "error_code": None,
        "message": "ok",
        "data": {
            "items": [user_payload(1), user_payload(2)],
            "meta": {"page": 1, "page_size": 2, "total_items": 4, "total_pages": 2},
        },
    }
    page2 = {
        "success": True,
        "error_code": None,
        "message": "ok",
        "data": {
            "items": [user_payload(3), user_payload(4)],
            "meta": {"page": 2, "page_size": 2, "total_items": 4, "total_pages": 2},
        },
    }

    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        side_effect=[Response(200, json=page1), Response(200, json=page2)]
    )

    synced, stale = sync_svc.sync_users(db_session)
    assert synced == 4
    assert db_session.query(UserSnapshot).count() == 4


def test_service_token_header_creation():
    sync_svc = SyncService()
    headers = sync_svc._headers()
    assert "Authorization" in headers
    auth = headers["Authorization"]
    assert auth.startswith("Bearer ")
    token = auth.removeprefix("Bearer ")

    payload = jwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    assert payload["sub"] == "report_service"
    assert payload["role"] == "admin"
    assert payload["email"] == "sync@report-service.internal"
    assert payload["exp"] > payload["iat"]


@respx.mock
@pytest.mark.parametrize("status_code", [401, 403])
def test_upstream_auth_failure_raises_sync_failed(db_session, status_code):
    sync_svc = SyncService()
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        return_value=Response(status_code, json={"message": "Unauthorized"})
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_users(db_session)

    assert exc_info.value.status_code == 502
    assert exc_info.value.error_code == "err_502_sync_failed"
    assert f"HTTP {status_code}" in exc_info.value.message


@respx.mock
def test_upstream_404_raises_sync_failed(db_session):
    sync_svc = SyncService()
    respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/product").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_products(db_session)

    assert exc_info.value.status_code == 502
    assert "HTTP 404" in exc_info.value.message


@respx.mock
@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_upstream_5xx_raises_sync_failed(db_session, status_code):
    sync_svc = SyncService()
    respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/order").mock(
        return_value=Response(status_code, text="Internal Server Error")
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_orders(db_session)

    assert exc_info.value.status_code == 502
    assert f"HTTP {status_code}" in exc_info.value.message


@respx.mock
def test_network_connection_error_raises_sync_failed(db_session):
    import httpx

    sync_svc = SyncService()
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        side_effect=httpx.ConnectError("Connection refused to port 8080")
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_users(db_session)

    assert exc_info.value.status_code == 502
    assert "Network error" in exc_info.value.message


@respx.mock
def test_timeout_raises_sync_failed(db_session):
    import httpx

    sync_svc = SyncService()
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        side_effect=httpx.ConnectTimeout("Connection timed out after 30s")
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_users(db_session)

    assert exc_info.value.status_code == 502
    assert "timed out" in exc_info.value.message


@respx.mock
def test_invalid_json_body_raises_sync_failed(db_session):
    sync_svc = SyncService()
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        return_value=Response(200, text="<html>502 Bad Gateway from NGINX</html>")
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_users(db_session)

    assert exc_info.value.status_code == 502
    assert "Invalid JSON" in exc_info.value.message


@respx.mock
def test_malformed_envelope_missing_items_raises_sync_failed(db_session):
    sync_svc = SyncService()
    # Missing 'items' key in data
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        return_value=Response(200, json={"success": True, "data": {"meta": {"total_pages": 1}}})
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_users(db_session)

    assert exc_info.value.status_code == 502
    assert "Missing 'items'" in exc_info.value.message


@respx.mock
def test_malformed_record_field_raises_sync_failed(db_session):
    sync_svc = SyncService()
    # Malformed user missing username
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        return_value=Response(
            200,
            json=api_page([{"id": 1, "email": "a@example.com", "role": "customer", "is_active": True}]),
        )
    )

    with pytest.raises(SyncFailed) as exc_info:
        sync_svc.sync_users(db_session)

    assert exc_info.value.status_code == 502
    assert "Invalid user record structure" in exc_info.value.message


@respx.mock
def test_atomic_rollback_on_failure_preserves_existing_snapshots(db_session):
    sync_svc = SyncService()

    # Pre-populate database with an initial snapshot
    init_user = UserSnapshot(
        user_id=100,
        username="original_user",
        email="orig@example.com",
        role="customer",
        is_active=True,
        created_at=datetime(2026, 1, 1),
    )
    init_prod = ProductSnapshot(
        product_id=200,
        name="Original Product",
        price=Decimal("10.00"),
        stock_quantity=50,
        is_active=True,
    )
    init_order = OrderSnapshot(
        order_id=300,
        user_id=100,
        status="completed",
        total_amount=Decimal("10.00"),
        created_at=datetime(2026, 1, 1),
    )
    db_session.add_all([init_user, init_prod, init_order])
    db_session.commit()

    # Stage 1: users returns new user 999
    respx.get(f"{settings.USER_SERVICE_BASE_URL}/user").mock(
        return_value=Response(200, json=api_page([user_payload(999)]))
    )
    # Stage 2: products fails with 500
    respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/product").mock(
        return_value=Response(500, text="Internal Error")
    )
    # Stage 3: orders not even reached
    respx.get(f"{settings.OPERATION_SERVICE_BASE_URL}/order").mock(
        return_value=Response(200, json=api_page([]))
    )

    with pytest.raises(SyncFailed):
        sync_svc.sync_all(db_session)

    # Database must have rolled back: original records MUST remain, new user 999 must NOT be committed
    assert db_session.get(UserSnapshot, 100) is not None
    assert db_session.get(UserSnapshot, 100).username == "original_user"
    assert db_session.get(UserSnapshot, 999) is None

    assert db_session.get(ProductSnapshot, 200) is not None
    assert db_session.get(OrderSnapshot, 300) is not None
