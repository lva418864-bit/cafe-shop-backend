"""
Shared, test-only helpers for the coffee_shop services.

NEVER import this package from application code (``*/app/**``). It is put on
``sys.path`` only by each service's pytest configuration (``pythonpath`` in
``pytest.ini``), and the CI mock-data check skips ``libs/testing/``.

Test-data marker convention (used by seed + cleanup scripts too):
  * users:     email prefix ``test_``, domain ``example.com``
  * products:  name prefix ``[TEST] ``
  * categories: name prefix ``[TEST] ``
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

TEST_EMAIL_DOMAIN = "example.com"
TEST_NAME_PREFIX = "[TEST] "
# Matches the dev environment JWT secret key used in local testing
TEST_JWT_SECRET = "dev-only-shared-secret-change-me"
TEST_JWT_ALGORITHM = "HS256"


def marked_email(local_part: str) -> str:
    clean_local = local_part.removeprefix("test_")
    return f"test_{clean_local}@{TEST_EMAIL_DOMAIN}"


def make_token(
    sub: int | str,
    role: str = "customer",
    email: str | None = None,
    secret: str = TEST_JWT_SECRET,
    expires_minutes: int = 30,
) -> str:
    """Mint a JWT shaped exactly like the ones user_service issues."""
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": str(sub),
        "email": email if email is not None else marked_email(f"user{sub}"),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(claims, secret, algorithm=TEST_JWT_ALGORITHM)


def auth_header(sub: int | str, role: str = "customer", **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub, role, **kwargs)}"}


# --------------------------------------------------------------------------
# Contract fixtures: payloads shaped like the real public responses of
# user_service / operation_service (see their *_schema.py). report_service
# tests feed these to respx so the sync is tested against the real contract.
# --------------------------------------------------------------------------
def api_page(items: list[dict], page: int = 1, page_size: int = 100, total_items: int | None = None) -> dict:
    total = len(items) if total_items is None else total_items
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "success": True,
        "error_code": None,
        "message": "ok",
        "data": {
            "items": items,
            "meta": {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages},
        },
    }


def user_payload(
    user_id: int, role: str = "customer", is_active: bool = True, created_at: str = "2026-01-15T10:00:00"
) -> dict:
    return {
        "id": user_id,
        "username": f"test_user{user_id}",
        "email": marked_email(f"user{user_id}"),
        "role": role,
        "is_active": is_active,
        "created_at": created_at,
    }


def product_payload(product_id: int, price: str = "4.00", stock_quantity: int = 20, is_active: bool = True) -> dict:
    return {
        "id": product_id,
        "name": f"{TEST_NAME_PREFIX}Product {product_id}",
        "description": None,
        "price": price,
        "stock_quantity": stock_quantity,
        "is_active": is_active,
        "category_id": 1,
        "category": {"id": 1, "name": f"{TEST_NAME_PREFIX}Category", "description": None},
        "customization_groups": [],
    }


def order_payload(
    order_id: int,
    user_id: int,
    status: str = "paid",
    items: list[tuple[int, int, str]] | None = None,
    created_at: str = "2026-01-20T09:30:00",
) -> dict:
    """items: list of (product_id, quantity, unit_price)."""
    items = items or [(1, 2, "4.00")]
    item_payloads = []
    total = 0.0
    for idx, (product_id, qty, unit_price) in enumerate(items, start=1):
        subtotal = float(unit_price) * qty
        total += subtotal
        item_payloads.append(
            {
                "id": order_id * 100 + idx,
                "product_id": product_id,
                "product_name": f"{TEST_NAME_PREFIX}Product {product_id}",
                "unit_price": unit_price,
                "quantity": qty,
                "subtotal": f"{subtotal:.2f}",
                "customizations": [],
            }
        )
    return {
        "id": order_id,
        "user_id": user_id,
        "status": status,
        "total_amount": f"{total:.2f}",
        "created_at": created_at,
        "items": item_payloads,
    }
