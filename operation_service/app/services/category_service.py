from sqlalchemy.orm import Session

from app.core.exception import ApplicationError
from app.repository.category_repository import CategoryRepository
from app.schemas.category_schema import CreateCategoryRequest, UpdateCategoryRequest


class CategoryAlreadyExists(ApplicationError):
    def __init__(self, name: str):
        super().__init__(
            message=f"Category '{name}' already exists", error_code="err_400_category_exists", status_code=400
        )


class CategoryNotFound(ApplicationError):
    def __init__(self, category_id: int):
        super().__init__(
            message=f"Category {category_id} not found", error_code="err_404_category_not_found", status_code=404
        )


class CategoryInUse(ApplicationError):
    def __init__(self):
        super().__init__(
            message="Cannot delete category that is currently assigned to products",
            error_code="err_400_category_in_use",
            status_code=400,
        )


class CategoryService:
    def __init__(self):
        self.repo = CategoryRepository()

    def get_all(self, db: Session):
        return self.repo.get_all(db)

    def get_by_id(self, db: Session, category_id: int):
        category = self.repo.get_by_id(db, category_id)
        if not category:
            raise CategoryNotFound(category_id)
        return category

    def create(self, db: Session, request: CreateCategoryRequest):
        existing = self.repo.get_by_name(db, request.name)
        if existing:
            raise CategoryAlreadyExists(request.name)
        return self.repo.create(db, request)

    def update(self, db: Session, category_id: int, request: UpdateCategoryRequest):
        category = self.get_by_id(db, category_id)
        if category.name != request.name:
            existing = self.repo.get_by_name(db, request.name)
            if existing:
                raise CategoryAlreadyExists(request.name)
        return self.repo.update(db, category, request)

    def delete(self, db: Session, category_id: int):
        category = self.get_by_id(db, category_id)
        if category.products:
            raise CategoryInUse()
        self.repo.delete(db, category)
        return category
