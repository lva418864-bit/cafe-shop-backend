from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies.auth import CurrentUser, require_roles
from app.dependencies.db import get_db
from app.schemas.api_response import ApiResponse
from app.schemas.report_schema import (
    DailySalesPoint,
    InventoryAlertResponse,
    SalesSummaryResponse,
    SyncResultResponse,
    TopProductResponse,
    UserSummaryResponse,
)
from app.services.report_service import ReportService
from app.services.sync_service import SyncService

router = APIRouter(prefix="/report", tags=["report"])

report_service = ReportService()
sync_service = SyncService()


def _default_range(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    """Default to the trailing 30 days when no range is given."""
    resolved_end = end_date or date.today()
    resolved_start = start_date or (resolved_end - timedelta(days=30))
    return resolved_start, resolved_end


@router.post("/sync", response_model=ApiResponse[SyncResultResponse])
def trigger_sync(
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    """
    Manually pull the latest users/products/orders from user_service and
    operation_service into report_service's own database. In a real
    deployment this would also be wired up to run on a schedule (cron /
    APScheduler / a message-queue consumer) instead of only at startup and
    on demand.
    """
    result = sync_service.sync_all(db)
    return ApiResponse(success=True, message="Sync completed successfully", data=SyncResultResponse(**result))


@router.get("/sales-summary", response_model=ApiResponse[SalesSummaryResponse])
def sales_summary(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    start_date, end_date = _default_range(start_date, end_date)
    data = report_service.sales_summary(db, start_date, end_date)
    return ApiResponse(success=True, message="Sales summary generated successfully", data=data)


@router.get("/daily-sales", response_model=ApiResponse[list[DailySalesPoint]])
def daily_sales(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    start_date, end_date = _default_range(start_date, end_date)
    data = report_service.daily_sales(db, start_date, end_date)
    return ApiResponse(success=True, message="Daily sales report generated successfully", data=data)


@router.get("/top-products", response_model=ApiResponse[list[TopProductResponse]])
def top_products(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    start_date, end_date = _default_range(start_date, end_date)
    data = report_service.top_products(db, start_date, end_date, limit=limit)
    return ApiResponse(success=True, message="Top products report generated successfully", data=data)


@router.get("/low-stock", response_model=ApiResponse[list[InventoryAlertResponse]])
def low_stock(
    threshold: int = Query(10, ge=0),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    data = report_service.low_stock_alerts(db, threshold=threshold)
    return ApiResponse(success=True, message="Low stock report generated successfully", data=data)


@router.get("/users-summary", response_model=ApiResponse[UserSummaryResponse])
def users_summary(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    start_date, end_date = _default_range(start_date, end_date)
    data = report_service.user_summary(db, start_date, end_date)
    return ApiResponse(success=True, message="User summary generated successfully", data=data)
