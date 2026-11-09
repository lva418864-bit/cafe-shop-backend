from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exception import Forbidden
from app.dependencies.auth import CurrentUser, get_current_user, require_roles
from app.dependencies.db import get_db
from app.schemas.api_response import ApiResponse, PageMeta, PaginatedData
from app.schemas.order_schema import CreateOrderRequest, OrderResponse, UpdateOrderStatusRequest
from app.services.order_service import OrderService

router = APIRouter(prefix="/order", tags=["order"])

order_service = OrderService()


@router.post("", response_model=ApiResponse[OrderResponse])
def create_order(
    request: CreateOrderRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    order = order_service.create_order(db, current_user.id, request)
    return ApiResponse(success=True, message="Order created successfully", data=order)


@router.get("", response_model=ApiResponse[PaginatedData[OrderResponse]])
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, pattern="^(pending|paid|cancelled|completed)$"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Customers only ever see their own orders; staff/admin can see everyone's."""
    user_filter = None if current_user.role in ("admin", "staff") else current_user.id
    items, meta = order_service.list_orders(db, page=page, page_size=page_size, user_id=user_filter, status=status)
    return ApiResponse(
        success=True,
        message="Orders fetched successfully",
        data=PaginatedData(items=items, meta=PageMeta(**meta)),
    )


@router.get("/{order_id}", response_model=ApiResponse[OrderResponse])
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    order = order_service.get_order_by_id(db, order_id)
    if current_user.role not in ("admin", "staff") and order.user_id != current_user.id:
        raise Forbidden("You may only view your own orders")
    return ApiResponse(success=True, message="Order fetched successfully", data=order)


@router.patch("/{order_id}/status", response_model=ApiResponse[OrderResponse])
def update_order_status(
    order_id: int,
    request: UpdateOrderStatusRequest,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    order = order_service.update_status(db, order_id, request.status)
    return ApiResponse(success=True, message="Order status updated successfully", data=order)


@router.post("/{order_id}/cancel", response_model=ApiResponse[OrderResponse])
def cancel_own_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Convenience endpoint for a customer to cancel their own pending order."""
    order = order_service.get_order_by_id(db, order_id)
    if current_user.role not in ("admin", "staff") and order.user_id != current_user.id:
        raise Forbidden("You may only cancel your own orders")

    order = order_service.update_status(db, order_id, "cancelled")
    return ApiResponse(success=True, message="Order cancelled successfully", data=order)
