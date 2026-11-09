from datetime import datetime

from sqlalchemy.orm import Session

from app.models.user import User


def get_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()


def create(db: Session, user: User):
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_all(db: Session, skip: int = 0, limit: int = 20):
    return db.query(User).filter(User.deleted_at.is_(None)).order_by(User.id).offset(skip).limit(limit).all()


def count_all(db: Session) -> int:
    return db.query(User).filter(User.deleted_at.is_(None)).count()


def get_by_id(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()


def update(db: Session, user: User):
    db.commit()
    db.refresh(user)
    return user


def soft_delete(db: Session, user: User):
    user.deleted_at = datetime.now()
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user
