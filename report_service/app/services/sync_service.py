from datetime import datetime
import json
import logging

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exception import SyncFailed
from app.core.security import create_service_token
from app.models.snapshot import OrderItemSnapshot, OrderSnapshot, ProductSnapshot, UserSnapshot

logger = logging.getLogger(__name__)


class SyncService:
    """
    Synchronizes local report snapshots with upstream source-of-truth services.

    Pulls from user_service and operation_service via authenticated HTTP calls,
    upserts updated records, and removes stale records that no longer exist
    upstream (e.g. after deletion or account closure) to prevent data drift.
    """

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {create_service_token()}"}

    def _paginate(self, base_url: str, path: str, params: dict | None = None):
        """Yields every item across all pages of a paginated ApiResponse endpoint."""
        page = 1
        params = dict(params or {})
        timeout = getattr(settings, "SYNC_TIMEOUT_SECONDS", 30.0)

        try:
            with httpx.Client(base_url=base_url, timeout=timeout) as client:
                while True:
                    params.update({"page": page, "page_size": settings.SYNC_PAGE_SIZE})
                    try:
                        response = client.get(path, params=params, headers=self._headers())
                    except httpx.TimeoutException as exc:
                        logger.error(
                            "Sync request timed out connecting to %s%s after %.1fs: %s",
                            base_url,
                            path,
                            timeout,
                            type(exc).__name__,
                        )
                        raise SyncFailed(base_url, f"Request timed out connecting to {path}") from exc
                    except httpx.RequestError as exc:
                        logger.error(
                            "Sync network error connecting to %s%s: %s (%s)",
                            base_url,
                            path,
                            type(exc).__name__,
                            str(exc),
                        )
                        raise SyncFailed(base_url, f"Network error connecting to {path}: {type(exc).__name__}") from exc

                    if response.status_code != 200:
                        logger.error(
                            "Sync upstream %s%s responded with HTTP %d: %s",
                            base_url,
                            path,
                            response.status_code,
                            response.text[:200],
                        )
                        raise SyncFailed(base_url, f"Upstream returned HTTP {response.status_code} for {path}")

                    try:
                        data = response.json()
                    except (json.JSONDecodeError, ValueError) as exc:
                        logger.error("Sync upstream %s%s returned non-JSON response body", base_url, path)
                        raise SyncFailed(base_url, f"Invalid JSON response from {path}") from exc

                    if not isinstance(data, dict) or "data" not in data or not isinstance(data["data"], dict):
                        logger.error("Sync upstream %s%s returned unexpected payload envelope: %s", base_url, path, type(data))
                        raise SyncFailed(base_url, f"Malformed response envelope from {path}")

                    payload = data["data"]
                    if "items" not in payload or not isinstance(payload["items"], list):
                        logger.error("Sync upstream %s%s missing 'items' list in payload", base_url, path)
                        raise SyncFailed(base_url, f"Missing 'items' array in response from {path}")

                    for item in payload["items"]:
                        if not isinstance(item, dict):
                            logger.error("Sync upstream %s%s returned non-dict item", base_url, path)
                            raise SyncFailed(base_url, f"Invalid item format in response from {path}")
                        yield item

                    meta = payload.get("meta")
                    if not isinstance(meta, dict) or "total_pages" not in meta:
                        logger.error("Sync upstream %s%s missing 'meta.total_pages'", base_url, path)
                        raise SyncFailed(base_url, f"Missing pagination metadata from {path}")

                    total_pages = meta["total_pages"]
                    if not isinstance(total_pages, int) or total_pages < 0:
                        logger.error("Sync upstream %s%s invalid total_pages: %s", base_url, path, total_pages)
                        raise SyncFailed(base_url, f"Invalid total_pages in response from {path}")

                    if page >= total_pages or total_pages == 0:
                        break
                    page += 1
        except SyncFailed:
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error during sync pagination from %s%s: %s (%s)",
                base_url,
                path,
                type(exc).__name__,
                str(exc),
            )
            raise SyncFailed(base_url, f"Unexpected error during sync from {path}: {type(exc).__name__}") from exc

    @staticmethod
    def _parse_naive_datetime(raw: str) -> datetime:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    # ------------------------------------------------------------- users
    def sync_users(self, db: Session, auto_commit: bool = True) -> tuple[int, int]:
        """Synchronizes users and removes stale snapshots. Returns (synced_count, stale_removed_count)."""
        seen_ids: set[int] = set()
        synced_count = 0

        try:
            for u in self._paginate(settings.USER_SERVICE_BASE_URL, "/user"):
                try:
                    user_id = int(u["id"])
                    seen_ids.add(user_id)
                    existing = db.get(UserSnapshot, user_id)
                    created_at = self._parse_naive_datetime(str(u["created_at"]))
                    username = str(u["username"])
                    email = str(u["email"])
                    role = str(u["role"])
                    is_active = bool(u["is_active"])
                except (KeyError, ValueError, TypeError) as exc:
                    logger.error("Invalid user snapshot record from upstream: %s (%s)", u, exc)
                    raise SyncFailed(settings.USER_SERVICE_BASE_URL, f"Invalid user record structure: {exc}") from exc

                if existing:
                    existing.username = username
                    existing.email = email
                    existing.role = role
                    existing.is_active = is_active
                    existing.created_at = created_at
                    existing.synced_at = datetime.now()
                else:
                    db.add(
                        UserSnapshot(
                            user_id=user_id,
                            username=username,
                            email=email,
                            role=role,
                            is_active=is_active,
                            created_at=created_at,
                            synced_at=datetime.now(),
                        )
                    )
                synced_count += 1

            # Purge snapshots that no longer exist in the upstream source of truth
            existing_snapshot_ids = {row[0] for row in db.query(UserSnapshot.user_id).all()}
            stale_ids = existing_snapshot_ids - seen_ids
            stale_count = len(stale_ids)

            if stale_ids:
                db.query(UserSnapshot).filter(UserSnapshot.user_id.in_(stale_ids)).delete(synchronize_session=False)

            if auto_commit:
                db.commit()
            return synced_count, stale_count
        except Exception:
            if auto_commit:
                db.rollback()
            raise

    # ---------------------------------------------------------- products
    def sync_products(self, db: Session, auto_commit: bool = True) -> tuple[int, int]:
        """Synchronizes products and removes stale snapshots. Returns (synced_count, stale_removed_count)."""
        seen_ids: set[int] = set()
        synced_count = 0

        try:
            for p in self._paginate(settings.OPERATION_SERVICE_BASE_URL, "/product", params={"active_only": False}):
                try:
                    product_id = int(p["id"])
                    seen_ids.add(product_id)
                    existing = db.get(ProductSnapshot, product_id)
                    name = str(p["name"])
                    price = p["price"]
                    stock_quantity = int(p["stock_quantity"])
                    is_active = bool(p["is_active"])
                except (KeyError, ValueError, TypeError) as exc:
                    logger.error("Invalid product snapshot record from upstream: %s (%s)", p, exc)
                    raise SyncFailed(settings.OPERATION_SERVICE_BASE_URL, f"Invalid product record structure: {exc}") from exc

                if existing:
                    existing.name = name
                    existing.price = price
                    existing.stock_quantity = stock_quantity
                    existing.is_active = is_active
                    existing.synced_at = datetime.now()
                else:
                    db.add(
                        ProductSnapshot(
                            product_id=product_id,
                            name=name,
                            price=price,
                            stock_quantity=stock_quantity,
                            is_active=is_active,
                            synced_at=datetime.now(),
                        )
                    )
                synced_count += 1

            existing_snapshot_ids = {row[0] for row in db.query(ProductSnapshot.product_id).all()}
            stale_ids = existing_snapshot_ids - seen_ids
            stale_count = len(stale_ids)

            if stale_ids:
                db.query(ProductSnapshot).filter(ProductSnapshot.product_id.in_(stale_ids)).delete(
                    synchronize_session=False
                )

            if auto_commit:
                db.commit()
            return synced_count, stale_count
        except Exception:
            if auto_commit:
                db.rollback()
            raise

    # ------------------------------------------------------------ orders
    def sync_orders(self, db: Session, auto_commit: bool = True) -> tuple[int, int]:
        """Synchronizes orders and removes stale snapshots. Returns (synced_count, stale_removed_count)."""
        seen_ids: set[int] = set()
        synced_count = 0

        try:
            for o in self._paginate(settings.OPERATION_SERVICE_BASE_URL, "/order"):
                try:
                    order_id = int(o["id"])
                    seen_ids.add(order_id)
                    user_id = int(o["user_id"])
                    status = str(o["status"])
                    total_amount = o["total_amount"]
                    created_at = self._parse_naive_datetime(str(o["created_at"]))
                    raw_items = o.get("items")
                    if raw_items is None or not isinstance(raw_items, list):
                        raise ValueError(f"Order {order_id} missing items list")
                except (KeyError, ValueError, TypeError) as exc:
                    logger.error("Invalid order snapshot record from upstream: %s (%s)", o, exc)
                    raise SyncFailed(settings.OPERATION_SERVICE_BASE_URL, f"Invalid order record structure: {exc}") from exc

                existing = db.get(OrderSnapshot, order_id)
                if existing:
                    existing.user_id = user_id
                    existing.status = status
                    existing.total_amount = total_amount
                    existing.created_at = created_at
                    existing.synced_at = datetime.now()
                    existing.items.clear()
                else:
                    existing = OrderSnapshot(
                        order_id=order_id,
                        user_id=user_id,
                        status=status,
                        total_amount=total_amount,
                        created_at=created_at,
                        synced_at=datetime.now(),
                    )
                    db.add(existing)
                    db.flush()

                for raw_item in raw_items:
                    try:
                        db.add(
                            OrderItemSnapshot(
                                order_id=order_id,
                                product_id=int(raw_item["product_id"]),
                                product_name=str(raw_item["product_name"]),
                                unit_price=raw_item["unit_price"],
                                quantity=int(raw_item["quantity"]),
                                subtotal=raw_item["subtotal"],
                            )
                        )
                    except (KeyError, ValueError, TypeError) as exc:
                        logger.error("Invalid order item snapshot record from upstream: %s (%s)", raw_item, exc)
                        raise SyncFailed(
                            settings.OPERATION_SERVICE_BASE_URL,
                            f"Invalid order item record structure: {exc}",
                        ) from exc

                synced_count += 1

            existing_snapshot_ids = {row[0] for row in db.query(OrderSnapshot.order_id).all()}
            stale_ids = existing_snapshot_ids - seen_ids
            stale_count = len(stale_ids)

            if stale_ids:
                db.query(OrderItemSnapshot).filter(OrderItemSnapshot.order_id.in_(stale_ids)).delete(
                    synchronize_session=False
                )
                db.query(OrderSnapshot).filter(OrderSnapshot.order_id.in_(stale_ids)).delete(synchronize_session=False)

            if auto_commit:
                db.commit()
            return synced_count, stale_count
        except Exception:
            if auto_commit:
                db.rollback()
            raise

    def sync_all(self, db: Session) -> dict:
        """
        Coordinates full snapshot synchronization atomically across all services.
        All stages (users, products, orders) are executed within a single transaction.
        If any stage fails, the entire transaction rolls back, leaving existing snapshots intact.
        """
        try:
            u_synced, u_stale = self.sync_users(db, auto_commit=False)
            p_synced, p_stale = self.sync_products(db, auto_commit=False)
            o_synced, o_stale = self.sync_orders(db, auto_commit=False)
            db.commit()
            logger.info(
                "Sync completed successfully: users=%d (removed %d), products=%d (removed %d), orders=%d (removed %d)",
                u_synced,
                u_stale,
                p_synced,
                p_stale,
                o_synced,
                o_stale,
            )
            return {
                "users_synced": u_synced,
                "products_synced": p_synced,
                "orders_synced": o_synced,
                "stale_users_removed": u_stale,
                "stale_products_removed": p_stale,
                "stale_orders_removed": o_stale,
            }
        except Exception:
            db.rollback()
            raise
