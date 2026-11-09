from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.snapshot import UserSnapshot


def count_total(db: Session) -> int:
    return db.query(UserSnapshot).count()


def count_active(db: Session) -> int:
    return db.query(UserSnapshot).filter(UserSnapshot.is_active.is_(True)).count()


def count_new_in_range(db: Session, start_date: date, end_date: date) -> int:
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())
    return db.query(UserSnapshot).filter(UserSnapshot.created_at >= start_dt, UserSnapshot.created_at <= end_dt).count()


def count_by_role(db: Session) -> dict[str, int]:
    rows = db.query(UserSnapshot.role, func.count(UserSnapshot.user_id)).group_by(UserSnapshot.role).all()
    return {role: count for role, count in rows}
