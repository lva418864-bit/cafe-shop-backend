from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.exception import Forbidden, PaymentNotFound
from app.dependencies.auth import CurrentUser, get_current_user, require_roles
from app.dependencies.db import get_db
from app.schemas.api_response import ApiResponse
from app.schemas.payment_schema import CreatePaymentRequest, PaymentResponse, RefundPaymentRequest
from app.services.order_service import OrderService
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/order/{order_id}/payment", tags=["payment"])

payment_service = PaymentService()
order_service = OrderService()


def _ensure_owner_or_staff(order_id: int, current_user: CurrentUser, db: Session):
    order = order_service.get_order_by_id(db, order_id)
    if current_user.role not in ("admin", "staff") and order.user_id != current_user.id:
        raise Forbidden("You may only pay for your own orders")
    return order


@router.post("", response_model=ApiResponse[PaymentResponse])
def pay_for_order(
    order_id: int,
    request: CreatePaymentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_owner_or_staff(order_id, current_user, db)
    payment = payment_service.pay_for_order(db, order_id, request, actor=f"{current_user.role}:{current_user.id}")
    msg = (
        "Bakong KHQR generated; awaiting customer scan"
        if payment.status == "pending"
        else "Payment processed successfully"
    )
    return ApiResponse(success=True, message=msg, data=payment)


@router.get("", response_model=ApiResponse[PaymentResponse])
def get_payment(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_owner_or_staff(order_id, current_user, db)
    payment = payment_service.get_payment_for_order(db, order_id)
    if payment is None:
        raise PaymentNotFound(order_id)
    return ApiResponse(success=True, message="Payment fetched successfully", data=payment)


@router.post("/verify", response_model=ApiResponse[PaymentResponse])
def verify_payment(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_owner_or_staff(order_id, current_user, db)
    payment = payment_service.verify_order_payment(db, order_id)
    return ApiResponse(success=True, message="Payment verification completed", data=payment)


@router.post("/regenerate", response_model=ApiResponse[PaymentResponse])
def regenerate_khqr(
    order_id: int,
    request: CreatePaymentRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_owner_or_staff(order_id, current_user, db)
    req = request or CreatePaymentRequest(method="khqr", currency="USD")
    payment = payment_service.initiate_khqr_payment(
        db,
        order_id,
        req,
        actor=f"{current_user.role}:{current_user.id}",
        force_regenerate=True,
    )
    return ApiResponse(success=True, message="Bakong KHQR regenerated successfully", data=payment)


@router.post("/cancel", response_model=ApiResponse[PaymentResponse])
def cancel_payment(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_owner_or_staff(order_id, current_user, db)
    payment = payment_service.cancel_order_payment(db, order_id, actor=f"{current_user.role}:{current_user.id}")
    return ApiResponse(success=True, message="Payment cancelled successfully", data=payment)


@router.post("/refund", response_model=ApiResponse[PaymentResponse])
def refund_payment(
    order_id: int,
    request: RefundPaymentRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    payment = payment_service.refund_order_payment(db, order_id, request, current_user.id)
    return ApiResponse(success=True, message="Manual Bakong refund recorded", data=payment)
