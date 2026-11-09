import math

from sqlalchemy.orm import Session

from app.core.exception import ProductNameAlreadyExists, ProductNotFound
from app.models.product import Product
from app.models.stock_movement import StockMovementReason
from app.repository import product_repository as product_repo
from app.schemas.product_schema import AdjustStockRequest, CreateProductRequest, UpdateProductRequest
from app.services.stock_service import StockService


from app.repository.category_repository import CategoryRepository
from app.services.category_service import CategoryNotFound


class ProductService:
    def __init__(self):
        self.stock_service = StockService()
        self.category_repo = CategoryRepository()

    # ---------------------------------------------------------------- create
    def add_product(self, db: Session, request: CreateProductRequest) -> Product:
        existing = product_repo.get_by_name(db, request.name)
        if existing:
            raise ProductNameAlreadyExists(request.name)

        category = self.category_repo.get_by_id(db, request.category_id)
        if not category:
            raise CategoryNotFound(request.category_id)

        product = Product(
            name=request.name.strip(),
            description=request.description,
            price=request.price,
            stock_quantity=0,
            is_active=True,
            category_id=request.category_id,
        )
        if request.customization_groups:
            from app.models.customization import CustomizationGroup, CustomizationOption

            for grp in request.customization_groups:
                cg = CustomizationGroup(name=grp.name, is_required=grp.is_required, selection_type=grp.selection_type)
                for opt in grp.options:
                    cg.options.append(CustomizationOption(name=opt.name, extra_price=opt.extra_price))
                product.customization_groups.append(cg)

        product = product_repo.create(db, product)

        if request.initial_stock > 0:
            self.stock_service.increase(
                db, product, request.initial_stock, reason=StockMovementReason.RESTOCK, reference="initial_stock"
            )

        return product

    # ------------------------------------------------------------------ read
    def list_products(self, db: Session, page: int = 1, page_size: int = 20, active_only: bool = False):
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        skip = (page - 1) * page_size

        total_items = product_repo.count_all(db, active_only=active_only)
        items = product_repo.get_all(db, skip=skip, limit=page_size, active_only=active_only)
        total_pages = math.ceil(total_items / page_size) if total_items else 0

        return items, {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        }

    def get_product_by_id(self, db: Session, product_id: int) -> Product:
        product = product_repo.get_by_id(db, product_id)
        if not product:
            raise ProductNotFound(product_id)
        return product

    # ---------------------------------------------------------------- update
    def update_product(self, db: Session, product_id: int, request: UpdateProductRequest) -> Product:
        product = self.get_product_by_id(db, product_id)

        if product.name != request.name:
            existing = product_repo.get_by_name(db, request.name)
            if existing:
                raise ProductNameAlreadyExists(request.name)

        category = self.category_repo.get_by_id(db, request.category_id)
        if not category:
            raise CategoryNotFound(request.category_id)

        product.name = request.name.strip()
        product.description = request.description
        product.price = request.price
        product.is_active = request.is_active
        product.category_id = request.category_id

        if request.customization_groups is not None:
            from app.models.customization import CustomizationGroup, CustomizationOption

            product.customization_groups.clear()
            for grp in request.customization_groups:
                cg = CustomizationGroup(name=grp.name, is_required=grp.is_required, selection_type=grp.selection_type)
                for opt in grp.options:
                    cg.options.append(CustomizationOption(name=opt.name, extra_price=opt.extra_price))
                product.customization_groups.append(cg)

        return product_repo.update(db, product)

    def adjust_stock(self, db: Session, product_id: int, request: AdjustStockRequest) -> Product:
        product = self.get_product_by_id(db, product_id)
        return self.stock_service.adjust(
            db, product, request.change_quantity, reason=request.reason, reference=request.reference
        )

    def deactivate(self, db: Session, product_id: int) -> Product:
        product = self.get_product_by_id(db, product_id)
        product.is_active = False
        return product_repo.update(db, product)
