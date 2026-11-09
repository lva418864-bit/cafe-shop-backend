from sqlalchemy.orm import Session

from app.core.exception import InsufficientStock, InvalidStockAdjustment
from app.models.product import Product
from app.models.stock_movement import StockMovement
from app.repository import product_repository as product_repo
from app.repository import stock_repository as stock_repo


class StockService:
    """
    Single choke point for every stock mutation in the system, so that
    product_service, order_service and payment_service never touch
    `Product.stock_quantity` directly - every change is validated and
    recorded in the stock_movements ledger.

    `commit=True` (default) is fine for standalone, single-step API calls
    (e.g. the manual restock endpoint). Callers that need to bundle several
    stock changes into one all-or-nothing transaction (e.g. order creation
    touching several products) should pass `commit=False` and take care of
    db.commit() / db.rollback() themselves.
    """

    def increase(
        self,
        db: Session,
        product: Product,
        quantity: int,
        reason: str,
        reference: str | None = None,
        commit: bool = True,
    ) -> Product:
        if quantity <= 0:
            raise InvalidStockAdjustment("Increase quantity must be a positive number")

        product.stock_quantity += quantity
        product_repo.update(db, product, commit=commit)
        self._record_movement(db, product, quantity, reason, reference, commit=commit)
        return product

    def decrease(
        self,
        db: Session,
        product: Product,
        quantity: int,
        reason: str,
        reference: str | None = None,
        commit: bool = True,
    ) -> Product:
        if quantity <= 0:
            raise InvalidStockAdjustment("Decrease quantity must be a positive number")
        if product.stock_quantity < quantity:
            raise InsufficientStock(product.id, requested=quantity, available=product.stock_quantity)

        product.stock_quantity -= quantity
        product_repo.update(db, product, commit=commit)
        self._record_movement(db, product, -quantity, reason, reference, commit=commit)
        return product

    def adjust(
        self,
        db: Session,
        product: Product,
        change_quantity: int,
        reason: str,
        reference: str | None = None,
        commit: bool = True,
    ) -> Product:
        """Generic signed adjustment used by the manual restock/adjustment API."""
        if change_quantity == 0:
            raise InvalidStockAdjustment()
        if change_quantity > 0:
            return self.increase(db, product, change_quantity, reason, reference, commit=commit)
        return self.decrease(db, product, -change_quantity, reason, reference, commit=commit)

    @staticmethod
    def _record_movement(
        db: Session, product: Product, change_quantity: int, reason: str, reference: str | None, commit: bool = True
    ):
        movement = StockMovement(
            product_id=product.id,
            change_quantity=change_quantity,
            quantity_after=product.stock_quantity,
            reason=reason,
            reference=reference,
        )
        stock_repo.create_movement(db, movement, commit=commit)
