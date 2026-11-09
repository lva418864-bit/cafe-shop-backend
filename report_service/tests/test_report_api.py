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
