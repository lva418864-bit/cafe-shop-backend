from datetime import datetime
from decimal import Decimal

from app.models.snapshot import OrderItemSnapshot, OrderSnapshot, UserSnapshot
from coffee_testing import TEST_NAME_PREFIX, auth_header, marked_email


def test_sales_summary_report(client, db_session):
    admin_headers = auth_header(sub=1, role="admin")

    # Add snapshot order
    order = OrderSnapshot(
        order_id=501,
        user_id=10,
        status="paid",
        total_amount=Decimal("15.50"),
        created_at=datetime.now(),
    )
    db_session.add(order)
    db_session.flush()

    item = OrderItemSnapshot(
        order_id=501,
        product_id=20,
        product_name=f"{TEST_NAME_PREFIX}Latte",
        unit_price=Decimal("5.00"),
        quantity=3,
        subtotal=Decimal("15.00"),
    )
    db_session.add(item)
    db_session.commit()

    res = client.get("/report/sales-summary", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total_orders"] >= 1
    assert data["paid_orders"] >= 1
    assert Decimal(str(data["total_revenue"])) >= Decimal("15.50")


def test_users_summary_report(client, db_session):
    admin_headers = auth_header(sub=1, role="admin")

    user_snap = UserSnapshot(
        user_id=901,
        username="test_customer_snap",
        email=marked_email("snap"),
        role="customer",
        is_active=True,
        created_at=datetime.now(),
    )
    db_session.add(user_snap)
    db_session.commit()

    res = client.get("/report/users-summary", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total_users"] >= 1
    assert data["active_users"] >= 1


def test_report_forbidden_for_regular_customer(client):
    customer_headers = auth_header(sub=10, role="customer")
    res = client.get("/report/sales-summary", headers=customer_headers)
    assert res.status_code == 403


def test_trigger_sync_unauthorized_without_token(client):
    res = client.post("/report/sync")
    assert res.status_code == 401
    body = res.json()
    assert body["success"] is False
    assert body["error_code"] == "err_401"


def test_trigger_sync_forbidden_for_customer(client):
    customer_headers = auth_header(sub=10, role="customer")
    res = client.post("/report/sync", headers=customer_headers)
    assert res.status_code == 403
    body = res.json()
    assert body["success"] is False
    assert body["error_code"] == "err_403"


def test_trigger_sync_success_contract(client, monkeypatch):
    from app.api import report_route

    dummy_result = {
        "users_synced": 5,
        "products_synced": 10,
        "orders_synced": 20,
        "stale_users_removed": 1,
        "stale_products_removed": 2,
        "stale_orders_removed": 3,
    }
    monkeypatch.setattr(report_route.sync_service, "sync_all", lambda _db: dummy_result)

    admin_headers = auth_header(sub=1, role="admin")
    res = client.post("/report/sync", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["message"] == "Sync completed successfully"
    data = body["data"]
    assert data["users_synced"] == 5
    assert data["products_synced"] == 10
    assert data["orders_synced"] == 20


def test_trigger_sync_staff_allowed(client, monkeypatch):
    from app.api import report_route

    dummy_result = {
        "users_synced": 1,
        "products_synced": 1,
        "orders_synced": 1,
        "stale_users_removed": 0,
        "stale_products_removed": 0,
        "stale_orders_removed": 0,
    }
    monkeypatch.setattr(report_route.sync_service, "sync_all", lambda _db: dummy_result)

    staff_headers = auth_header(sub=2, role="staff")
    res = client.post("/report/sync", headers=staff_headers)
    assert res.status_code == 200
    assert res.json()["success"] is True


def test_trigger_sync_returns_502_on_upstream_failure(client, monkeypatch):
    from app.api import report_route
    from app.core.exception import SyncFailed

    def _fail(_db):
        raise SyncFailed("http://user-service.railway.internal:8001", "Connection refused")

    monkeypatch.setattr(report_route.sync_service, "sync_all", _fail)

    admin_headers = auth_header(sub=1, role="admin")
    res = client.post("/report/sync", headers=admin_headers)
    assert res.status_code == 502
    body = res.json()
    assert body["success"] is False
    assert body["error_code"] == "err_502_sync_failed"
    assert "Failed to sync data" in body["message"]
    assert body["data"] is None
