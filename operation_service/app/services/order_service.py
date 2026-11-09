import math

from sqlalchemy.orm import Session

from app.core.exception import (
    EmptyOrder,
    InvalidOrderStatusTransition,
    OrderNotFound,
    ProductInactive,
    ProductNotFound,
)
from app.models.order import Order, OrderItem, OrderItemCustomization, OrderStatus
from app.models.stock_movement import StockMovementReason
from app.repository import order_repository as order_repo
from app.repository import product_repository as product_repo
from app.schemas.order_schema import CreateOrderRequest
from app.services.stock_service import StockService


class OrderService:
    def __init__(self):
        self.stock_service = StockService()

    # ---------------------------------------------------------------- create
    def create_order(self, db: Session, user_id: int, request: CreateOrderRequest) -> Order:
        if not request.items:
            raise EmptyOrder()

        try:
            order = Order(user_id=user_id, status=OrderStatus.PENDING, total_amount=0)
            db.add(order)
            db.flush()  # assign order.id, still inside the open transaction

            total_amount = 0.0
            for item in request.items:
                # Row-level lock prevents two concurrent orders from
                # overselling the last units of the same product.
                product = product_repo.get_by_id_for_update(db, item.product_id)
                if not product:
                    raise ProductNotFound(item.product_id)
                if not product.is_active:
                    raise ProductInactive(product.id)

                # commit=False: stock is decremented as part of the same
                # transaction as the order itself, so a failure on any line
                # item rolls back every stock change made so far too.
                self.stock_service.decrease(
                    db,
                    product,
                    item.quantity,
                    reason=StockMovementReason.SALE,
                    reference=f"order#{order.id}",
                    commit=False,
                )

                unit_price = float(product.price)

                # Fetch customizations to add to price
                item_customizations = []
                if item.customizations:
                    from app.models.customization import CustomizationGroup, CustomizationOption

                    for c_req in item.customizations:
                        group = (
                            db.query(CustomizationGroup)
                            .filter(
                                CustomizationGroup.id == c_req.group_id,
                                CustomizationGroup.product_id == product.id,
                            )
                            .first()
                        )
                        if not group:
                            continue
                        option = (
                            db.query(CustomizationOption)
                            .filter(
                                CustomizationOption.id == c_req.option_id,
                                CustomizationOption.group_id == group.id,
                            )
                            .first()
                        )
                        if option:
                            unit_price += float(option.extra_price)
                            item_customizations.append(
                                OrderItemCustomization(
                                    group_id=group.id,
                                    group_name=group.name,
                                    option_id=option.id,
                                    option_name=option.name,
                                    extra_price=float(option.extra_price),
                                )
                            )

                subtotal = unit_price * item.quantity
                total_amount += subtotal

                order_item = OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=unit_price,
                    quantity=item.quantity,
                    subtotal=subtotal,
                )
                if item_customizations:
                    order_item.customizations.extend(item_customizations)

                db.add(order_item)

            order.total_amount = total_amount
            db.commit()
            db.refresh(order)
            return order
        except Exception:
            db.rollback()
            raise

    # ------------------------------------------------------------------ read
    def list_orders(
        self,
        db: Session,
        page: int = 1,
        page_size: int = 20,
        user_id: int | None = None,
        status: str | None = None,
    ):
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        skip = (page - 1) * page_size

        total_items = order_repo.count_all(db, user_id=user_id, status=status)
        items = order_repo.get_all(db, skip=skip, limit=page_size, user_id=user_id, status=status)
        total_pages = math.ceil(total_items / page_size) if total_items else 0

        return items, {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        }

    def get_order_by_id(self, db: Session, order_id: int) -> Order:
        order = order_repo.get_by_id(db, order_id)
        if not order:
            raise OrderNotFound(order_id)
        return order

    # -------------------------------------------------------------- status
    def update_status(self, db: Session, order_id: int, new_status: str) -> Order:
        order = self.get_order_by_id(db, order_id)

        if new_status == order.status:
            return order

        allowed = OrderStatus.TRANSITIONS.get(order.status, set())
        if new_status not in allowed:
            raise InvalidOrderStatusTransition(order.status, new_status)

        try:
            if new_status == OrderStatus.CANCELLED:
                self._restock_items(db, order)

            order.status = new_status
            db.commit()
            db.refresh(order)
            return order
        except Exception:
            db.rollback()
            raise

    def _restock_items(self, db: Session, order: Order):
        for item in order.items:
            product = product_repo.get_by_id_for_update(db, item.product_id)
            if product is None:
                continue  # product may have been hard-deleted since; nothing to restock
            self.stock_service.increase(
                db,
                product,
                item.quantity,
                reason=StockMovementReason.CANCELLATION,
                reference=f"order#{order.id}",
                commit=False,
            )
