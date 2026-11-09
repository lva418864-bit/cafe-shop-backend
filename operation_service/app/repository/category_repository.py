from sqlalchemy.orm import Session

from app.models.category import Category
from app.schemas.category_schema import CreateCategoryRequest, UpdateCategoryRequest


class CategoryRepository:
    def get_all(self, db: Session):
        return db.query(Category).order_by(Category.name).all()

    def get_by_id(self, db: Session, category_id: int):
        return db.query(Category).filter(Category.id == category_id).first()

    def get_by_name(self, db: Session, name: str):
        return db.query(Category).filter(Category.name == name).first()

    def create(self, db: Session, request: CreateCategoryRequest):
        category = Category(name=request.name, description=request.description)
        db.add(category)
        db.commit()
        db.refresh(category)
        return category

    def update(self, db: Session, category: Category, request: UpdateCategoryRequest):
        category.name = request.name
        category.description = request.description
        db.commit()
        db.refresh(category)
        return category

    def delete(self, db: Session, category: Category):
        db.delete(category)
        db.commit()
