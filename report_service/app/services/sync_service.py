from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exception import SyncFailed
from app.core.security import create_service_token
from app.models.snapshot import OrderItemSnapshot, OrderSnapshot, ProductSnapshot, UserSnapshot


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
        try:
            with httpx.Client(base_url=base_url, timeout=10.0) as client:
                while True:
                    params.update({"page": page, "page_size": settings.SYNC_PAGE_SIZE})
                    response = client.get(path, params=params, headers=self._headers())
                    response.raise_for_status()
                    payload = response.json()["data"]
                    for item in payload["items"]:
                        yield item

                    total_pages = payload["meta"]["total_pages"]
                    if page >= total_pages or total_pages == 0:
                        break
                    page += 1
        except httpx.HTTPError as exc:
            raise SyncFailed(base_url, str(exc))

    @staticmethod
    def _parse_naive_datetime(raw: str) -> datetime:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    # ------------------------------------------------------------- users
    def sync_users(self, db: Session) -> tuple[int, int]:
        """Synchronizes users and removes stale snapshots. Returns (synced_count, stale_removed_count)."""
        seen_ids: set[int] = set()
        synced_count = 0

        try:
            for u in self._paginate(settings.USER_SERVICE_BASE_URL, "/user"):
                user_id = int(u["id"])
                seen_ids.add(user_id)
                existing = db.get(UserSnapshot, user_id)
                created_at = self._parse_naive_datetime(u["created_at"])
                if existing:
                    existing.username = u["username"]
                    existing.email = u["email"]
                    existing.role = u["role"]
                    existing.is_active = u["is_active"]
                    existing.created_at = created_at
                    existing.synced_at = datetime.now()
                else:
                    db.add(
                        UserSnapshot(
                            user_id=user_id,
                            username=u["username"],
                            email=u["email"],
                            role=u["role"],
                            is_active=u["is_active"],
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

            db.commit()
            return synced_count, stale_count
        except Exception:
            db.rollback()
            raise

    # ---------------------------------------------------------- products
    def sync_products(self, db: Session) -> tuple[int, int]:
        """Synchronizes products and removes stale snapshots. Returns (synced_count, stale_removed_count)."""
        seen_ids: set[int] = set()
        synced_count = 0

        try:
            for p in self._paginate(settings.OPERATION_SERVICE_BASE_URL, "/product", params={"active_only": False}):
                product_id = int(p["id"])
                seen_ids.add(product_id)
                existing = db.get(ProductSnapshot, product_id)
                if existing:
                    existing.name = p["name"]
                    existing.price = p["price"]
                    existing.stock_quantity = p["stock_quantity"]
                    existing.is_active = p["is_active"]
                    existing.synced_at = datetime.now()
                else:
                    db.add(
                        ProductSnapshot(
                            product_id=product_id,
                            name=p["name"],
                            price=p["price"],
                            stock_quantity=p["stock_quantity"],
                            is_active=p["is_active"],
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

            db.commit()
            return synced_count, stale_count
        except Exception:
            db.rollback()
            raise

    # ------------------------------------------------------------ orders
    def sync_orders(self, db: Session) -> tuple[int, int]:
        """Synchronizes orders and removes stale snapshots. Returns (synced_count, stale_removed_count)."""
        seen_ids: set[int] = set()
        synced_count = 0

        try:
            for o in self._paginate(settings.OPERATION_SERVICE_BASE_URL, "/order"):
                order_id = int(o["id"])
                seen_ids.add(order_id)
                existing = db.get(OrderSnapshot, order_id)
                created_at = self._parse_naive_datetime(o["created_at"])
                if existing:
                    existing.user_id = o["user_id"]
                    existing.status = o["status"]
                    existing.total_amount = o["total_amount"]
                    existing.created_at = created_at
                    existing.synced_at = datetime.now()
                    existing.items.clear()
                else:
                    existing = OrderSnapshot(
                        order_id=order_id,
                        user_id=o["user_id"],
                        status=o["status"],
                        total_amount=o["total_amount"],
                        created_at=created_at,
                        synced_at=datetime.now(),
                    )
                    db.add(existing)
                    db.flush()

                for item in o["items"]:
                    db.add(
                        OrderItemSnapshot(
                            order_id=order_id,
                            product_id=item["product_id"],
                            product_name=item["product_name"],
                            unit_price=item["unit_price"],
                            quantity=item["quantity"],
                            subtotal=item["subtotal"],
                        )
                    )
                synced_count += 1

            existing_snapshot_ids = {row[0] for row in db.query(OrderSnapshot.order_id).all()}
            stale_ids = existing_snapshot_ids - seen_ids
            stale_count = len(stale_ids)

            if stale_ids:
                db.query(OrderItemSnapshot).filter(OrderItemSnapshot.order_id.in_(stale_ids)).delete(
                    synchronize_session=False
                )
                db.query(OrderSnapshot).filter(OrderSnapshot.order_id.in_(stale_ids)).delete(synchronize_session=False)

            db.commit()
            return synced_count, stale_count
        except Exception:
            db.rollback()
            raise

    def sync_all(self, db: Session) -> dict:
        u_synced, u_stale = self.sync_users(db)
        p_synced, p_stale = self.sync_products(db)
        o_synced, o_stale = self.sync_orders(db)
        return {
            "users_synced": u_synced,
            "products_synced": p_synced,
            "orders_synced": o_synced,
            "stale_users_removed": u_stale,
            "stale_products_removed": p_stale,
            "stale_orders_removed": o_stale,
        }
