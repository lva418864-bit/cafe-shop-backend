from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.snapshot import OrderItemSnapshot, OrderSnapshot, ProductSnapshot

_REVENUE_STATUSES = ("paid", "completed")


def _range_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    return datetime.combine(start_date, datetime.min.time()), datetime.combine(end_date, datetime.max.time())


def count_orders_in_range(db: Session, start_date: date, end_date: date) -> int:
    start_dt, end_dt = _range_bounds(start_date, end_date)
    return (
        db.query(OrderSnapshot).filter(OrderSnapshot.created_at >= start_dt, OrderSnapshot.created_at <= end_dt).count()
    )


def count_orders_by_status_in_range(db: Session, start_date: date, end_date: date, status: str) -> int:
    start_dt, end_dt = _range_bounds(start_date, end_date)
    return (
        db.query(OrderSnapshot)
        .filter(
            OrderSnapshot.created_at >= start_dt, OrderSnapshot.created_at <= end_dt, OrderSnapshot.status == status
        )
        .count()
    )


def total_revenue_in_range(db: Session, start_date: date, end_date: date) -> float:
    start_dt, end_dt = _range_bounds(start_date, end_date)
    result = (
        db.query(func.coalesce(func.sum(OrderSnapshot.total_amount), 0))
        .filter(
            OrderSnapshot.created_at >= start_dt,
            OrderSnapshot.created_at <= end_dt,
            OrderSnapshot.status.in_(_REVENUE_STATUSES),
        )
        .scalar()
    )
    return float(result or 0)


def daily_sales_in_range(db: Session, start_date: date, end_date: date):
    start_dt, end_dt = _range_bounds(start_date, end_date)
    day_expr = func.date(OrderSnapshot.created_at)
    rows = (
        db.query(
            day_expr.label("day"),
            func.count(OrderSnapshot.order_id),
            func.coalesce(func.sum(OrderSnapshot.total_amount), 0),
        )
        .filter(
            OrderSnapshot.created_at >= start_dt,
            OrderSnapshot.created_at <= end_dt,
            OrderSnapshot.status.in_(_REVENUE_STATUSES),
        )
        .group_by(day_expr)
        .order_by(day_expr)
        .all()
    )
    return rows


def top_products_in_range(db: Session, start_date: date, end_date: date, limit: int = 10):
    start_dt, end_dt = _range_bounds(start_date, end_date)
    rows = (
        db.query(
            OrderItemSnapshot.product_id,
            OrderItemSnapshot.product_name,
            func.sum(OrderItemSnapshot.quantity),
            func.sum(OrderItemSnapshot.subtotal),
        )
        .join(OrderSnapshot, OrderSnapshot.order_id == OrderItemSnapshot.order_id)
        .filter(
            OrderSnapshot.created_at >= start_dt,
            OrderSnapshot.created_at <= end_dt,
            OrderSnapshot.status.in_(_REVENUE_STATUSES),
        )
        .group_by(OrderItemSnapshot.product_id, OrderItemSnapshot.product_name)
        .order_by(func.sum(OrderItemSnapshot.quantity).desc())
        .limit(limit)
        .all()
    )
    return rows


def low_stock_products(db: Session, threshold: int = 10):
    return (
        db.query(ProductSnapshot)
        .filter(ProductSnapshot.is_active.is_(True), ProductSnapshot.stock_quantity <= threshold)
        .order_by(ProductSnapshot.stock_quantity.asc())
        .all()
    )
