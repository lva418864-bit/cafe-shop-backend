from sqlalchemy.orm import Session

from app.models.order import Order


def create(db: Session, order: Order) -> Order:
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_by_id(db: Session, order_id: int):
    return db.query(Order).filter(Order.id == order_id).first()


def update(db: Session, order: Order) -> Order:
    db.commit()
    db.refresh(order)
    return order


def get_all(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    user_id: int | None = None,
    status: str | None = None,
):
    query = db.query(Order)
    if user_id is not None:
        query = query.filter(Order.user_id == user_id)
    if status is not None:
        query = query.filter(Order.status == status)
    return query.order_by(Order.id.desc()).offset(skip).limit(limit).all()


def count_all(db: Session, user_id: int | None = None, status: str | None = None) -> int:
    query = db.query(Order)
    if user_id is not None:
        query = query.filter(Order.user_id == user_id)
    if status is not None:
        query = query.filter(Order.status == status)
    return query.count()
