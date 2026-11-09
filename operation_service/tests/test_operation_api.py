from decimal import Decimal

from app.models.category import Category
from app.models.product import Product
from coffee_testing import TEST_NAME_PREFIX, auth_header


def test_list_products_public(client, db_session):
    cat = Category(name=f"{TEST_NAME_PREFIX}Coffee", description="Test Category")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}Cold Brew",
        description="Fresh cold brew",
        price=Decimal("4.00"),
        stock_quantity=10,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()

    res = client.get("/product")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["data"]["items"]) >= 1


def test_unconfigured_payment_gateway_returns_503(client, db_session):
    cat = Category(name=f"{TEST_NAME_PREFIX}Tea", description="Tea category")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}Green Tea",
        description="Organic green tea",
        price=Decimal("3.00"),
        stock_quantity=20,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    customer_headers = auth_header(sub=101, role="customer")
    order_res = client.post(
        "/order",
        headers=customer_headers,
        json={"items": [{"product_id": prod.id, "quantity": 2}]},
    )
    assert order_res.status_code == 200
    order_id = order_res.json()["data"]["id"]

    # Direct customer checkout returns 503 until an external gateway provider is integrated
    pay_res = client.post(
        f"/order/{order_id}/payment",
        headers=customer_headers,
        json={"method": "card"},
    )
    assert pay_res.status_code == 503
    assert "not configured" in pay_res.json()["message"]


def test_order_creation_and_staff_advances_status_to_paid(client, db_session):
    cat = Category(name=f"{TEST_NAME_PREFIX}Pastries", description="Pastries")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}Croissant",
        description="Butter croissant",
        price=Decimal("2.50"),
        stock_quantity=15,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()

    customer_headers = auth_header(sub=102, role="customer")
    order_res = client.post(
        "/order",
        headers=customer_headers,
        json={"items": [{"product_id": prod.id, "quantity": 2}]},
    )
    order_id = order_res.json()["data"]["id"]
    assert order_res.json()["data"]["status"] == "pending"

    # Verify inventory was decremented
    db_session.refresh(prod)
    assert prod.stock_quantity == 13

    # Staff verifies payment and advances status to paid
    staff_headers = auth_header(sub=2, role="staff")
    status_res = client.patch(
        f"/order/{order_id}/status",
        headers=staff_headers,
        json={"status": "paid"},
    )
    assert status_res.status_code == 200
    assert status_res.json()["data"]["status"] == "paid"

    # Order is now paid
    get_res = client.get(f"/order/{order_id}", headers=customer_headers)
    assert get_res.json()["data"]["status"] == "paid"


def test_public_catalog_and_media_require_no_token_and_omit_private_fields(
    client, db_session, tmp_path, monkeypatch
):
    from io import BytesIO
    from PIL import Image
    from app.core.config import settings

    upload_dir = tmp_path / "public_media_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))

    cat = Category(name=f"{TEST_NAME_PREFIX}PublicCat", description="Public category")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}PublicEspresso",
        description="Double shot espresso",
        price=Decimal("0.50"),
        stock_quantity=20,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    # Staff uploads an image so we can verify public product detail & media routes
    buf = BytesIO()
    Image.new("RGB", (48, 48), (90, 60, 30)).save(buf, format="JPEG")
    staff_headers = auth_header(sub=7, role="staff")
    up_res = client.post(
        f"/product/{prod.id}/images",
        headers=staff_headers,
        files={"file": ("espresso.jpg", buf.getvalue(), "image/jpeg")},
        data={"alt_text": "Espresso Shot"},
    )
    assert up_res.status_code == 200
    storage_key = up_res.json()["data"]["storage_key"]

    # 1. Public product list (no token) -> 200 and no private/staff fields
    list_res = client.get("/product")
    assert list_res.status_code == 200
    items = list_res.json()["data"]["items"]
    matching = [item for item in items if item["id"] == prod.id]
    assert len(matching) == 1
    public_item = matching[0]
    for forbidden_key in ("cost", "stock_movements", "created_by", "user_id", "orders"):
        assert forbidden_key not in public_item
    for img_meta in public_item.get("images", []):
        assert "created_by" not in img_meta

    # 2. Public product detail (no token) -> 200 and no private/staff fields
    detail_res = client.get(f"/product/{prod.id}")
    assert detail_res.status_code == 200
    detail_data = detail_res.json()["data"]
    assert detail_data["id"] == prod.id
    assert detail_data["price"] == "0.50"
    for forbidden_key in ("cost", "stock_movements", "created_by", "user_id", "orders"):
        assert forbidden_key not in detail_data
    assert len(detail_data["images"]) == 1
    assert "created_by" not in detail_data["images"][0]

    # 3. Public categories list (no token) -> 200
    cat_res = client.get("/category")
    assert cat_res.status_code == 200

    # 4. Public media file (no token) -> 200
    media_res = client.get(f"/media/{storage_key}")
    assert media_res.status_code == 200
    assert media_res.headers["content-type"] == "image/jpeg"


def test_order_and_payment_endpoints_require_jwt_and_work_with_customer_token(
    client, db_session, monkeypatch
):
    import respx
    from httpx import Response
    from app.core.config import settings
    from app.services.bakong_khqr import BakongOpenAPIClient

    cat = Category(name=f"{TEST_NAME_PREFIX}AuthOrderCat", description="Order auth test")
    db_session.add(cat)
    db_session.flush()

    prod = Product(
        name=f"{TEST_NAME_PREFIX}AuthOrderLatte",
        description="Latte for auth test",
        price=Decimal("0.50"),
        stock_quantity=15,
        is_active=True,
        category_id=cat.id,
    )
    db_session.add(prod)
    db_session.commit()
    db_session.refresh(prod)

    # 1. Guest (no token) calling create order, order list, or create payment gets 401
    unauth_create_order = client.post(
        "/order",
        json={"items": [{"product_id": prod.id, "quantity": 1}]},
    )
    assert unauth_create_order.status_code == 401

    unauth_list_orders = client.get("/order")
    assert unauth_list_orders.status_code == 401

    unauth_create_payment = client.post(
        "/order/1/payment",
        json={"method": "khqr", "currency": "USD"},
    )
    assert unauth_create_payment.status_code == 401

    # 2. Valid customer token works for creating order, listing orders, and initiating mocked KHQR payment
    test_base_url = "https://sit-api-bakong.nbc.gov.kh"
    monkeypatch.setattr(settings, "BAKONG_ACCOUNT_ID", "coffeeshop@aclb")
    monkeypatch.setattr(settings, "BAKONG_API_BASE_URL", test_base_url)
    monkeypatch.setattr(settings, "BAKONG_MAX_AMOUNT_USD", Decimal("1.00"))
    monkeypatch.setattr(
        "app.services.payment_service.get_bakong_client",
        lambda: BakongOpenAPIClient(
            base_url=test_base_url,
            account_id="coffeeshop@aclb",
            max_amount_usd=Decimal("1.00"),
            use_httpx=True,
        ),
    )

    customer_headers = auth_header(sub=305, role="customer")
    create_res = client.post(
        "/order",
        headers=customer_headers,
        json={"items": [{"product_id": prod.id, "quantity": 1}]},
    )
    assert create_res.status_code == 200
    order_id = create_res.json()["data"]["id"]

    list_res = client.get("/order", headers=customer_headers)
    assert list_res.status_code == 200
    assert any(o["id"] == order_id for o in list_res.json()["data"]["items"])

    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.post(f"{test_base_url}/v1/generate_deeplink_by_qr").mock(
            return_value=Response(
                200,
                json={"responseCode": 0, "data": {"shortLink": "https://bakong.nbc.gov.kh/mock"}},
            )
        )
        pay_res = client.post(
            f"/order/{order_id}/payment",
            headers=customer_headers,
            json={"method": "khqr", "currency": "USD"},
        )
    assert pay_res.status_code == 200
    assert pay_res.json()["data"]["status"] == "pending"
    assert len(pay_res.json()["data"]["qr_md5"]) == 32

