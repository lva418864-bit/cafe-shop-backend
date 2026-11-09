from sqlalchemy.orm import Session

from app.models.payment import Payment


def get_by_order_id(db: Session, order_id: int):
    return db.query(Payment).filter(Payment.order_id == order_id).first()


def create(db: Session, payment: Payment) -> Payment:
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def update(db: Session, payment: Payment) -> Payment:
    db.commit()
    db.refresh(payment)
    return payment
