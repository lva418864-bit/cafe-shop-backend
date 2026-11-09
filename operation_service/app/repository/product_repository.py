from sqlalchemy.orm import Session

from app.models.product import Product


def get_by_name(db: Session, name: str):
    return db.query(Product).filter(Product.name == name, Product.deleted_at.is_(None)).first()


def get_by_id(db: Session, product_id: int):
    return db.query(Product).filter(Product.id == product_id, Product.deleted_at.is_(None)).first()


def get_by_id_for_update(db: Session, product_id: int):
    """Row-level lock so concurrent orders can't oversell the same stock."""
    return db.query(Product).filter(Product.id == product_id, Product.deleted_at.is_(None)).with_for_update().first()


def create(db: Session, product: Product) -> Product:
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def update(db: Session, product: Product, commit: bool = True) -> Product:
    if commit:
        db.commit()
        db.refresh(product)
    else:
        db.flush()
    return product


def get_all(db: Session, skip: int = 0, limit: int = 20, active_only: bool = False):
    query = db.query(Product).filter(Product.deleted_at.is_(None))
    if active_only:
        query = query.filter(Product.is_active.is_(True))
    return query.order_by(Product.id).offset(skip).limit(limit).all()


def count_all(db: Session, active_only: bool = False) -> int:
    query = db.query(Product).filter(Product.deleted_at.is_(None))
    if active_only:
        query = query.filter(Product.is_active.is_(True))
    return query.count()
