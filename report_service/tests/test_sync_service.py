import respx
from httpx import Response

from app.core.config import settings
from app.models.snapshot import OrderSnapshot, ProductSnapshot, UserSnapshot
from app.services.sync_service import SyncService
from coffee_testing import api_page, order_payload, product_payload, user_payload


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
