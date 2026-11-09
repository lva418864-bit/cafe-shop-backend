from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.exception import Forbidden
from app.dependencies.auth import get_current_user, require_roles
from app.dependencies.db import get_db
from app.models.user import User
from app.schemas.api_response import ApiResponse, PageMeta, PaginatedData
from app.schemas.user_schema import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    TokenResponse,
    UpdateUserRequest,
    UpdateUserRoleRequest,
    UserResponse,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/user", tags=["user"])

user_service = UserService()


@router.post("", response_model=ApiResponse[UserResponse])
def register(request: CreateUserRequest, db: Session = Depends(get_db)):
    """Public self-registration. Password is hashed before it ever touches the DB."""
    user = user_service.create_user(db, request)
    return ApiResponse(success=True, message="User registered successfully", data=user)


@router.post("/login", response_model=ApiResponse[TokenResponse])
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = user_service.authenticate(db, request)
    token, expires_in_minutes = user_service.issue_token_for(user)
    return ApiResponse(
        success=True,
        message="Login successful",
        data=TokenResponse(
            access_token=token,
            expires_in_minutes=expires_in_minutes,
            user=user,
        ),
    )


@router.get("/me", response_model=ApiResponse[UserResponse])
def get_me(current_user: User = Depends(get_current_user)):
    return ApiResponse(success=True, message="Current user fetched successfully", data=current_user)


@router.get("", response_model=ApiResponse[PaginatedData[UserResponse]])
def read_all_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_roles("admin", "staff")),
):
    items, meta = user_service.get_all_user(db, page=page, page_size=page_size)
    return ApiResponse(
        success=True,
        message="Users fetched successfully",
        data=PaginatedData(items=items, meta=PageMeta(**meta)),
    )


@router.get("/{user_id}", response_model=ApiResponse[UserResponse])
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.id != user_id and current_user.role not in ("admin", "staff"):
        raise Forbidden("You may only view your own account")

    user = user_service.get_user_by_id(db, user_id)
    return ApiResponse(success=True, message="User fetched successfully", data=user)


@router.put("/{user_id}", response_model=ApiResponse[UserResponse])
def update_user(
    user_id: int,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.id != user_id and current_user.role != "admin":
        raise Forbidden("You may only update your own account")

    user = user_service.update_user(db, user_id, request)
    return ApiResponse(success=True, message="User updated successfully", data=user)


@router.patch("/{user_id}/role", response_model=ApiResponse[UserResponse])
def update_user_role(
    user_id: int,
    request: UpdateUserRoleRequest,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_roles("admin")),
):
    user = user_service.update_role(db, user_id, request.role)
    return ApiResponse(success=True, message="User role updated successfully", data=user)


@router.patch("/{user_id}/password", response_model=ApiResponse[UserResponse])
def change_password(
    user_id: int,
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.id != user_id:
        raise Forbidden("You may only change your own password")

    user = user_service.change_password(db, user_id, request)
    return ApiResponse(success=True, message="Password changed successfully", data=user)


@router.delete("/{user_id}", response_model=ApiResponse[UserResponse])
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_roles("admin")),
):
    user = user_service.delete_user(db, user_id)
    return ApiResponse(success=True, message="User deleted successfully", data=user)
