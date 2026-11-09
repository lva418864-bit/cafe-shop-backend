from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies.auth import CurrentUser, require_roles
from app.dependencies.db import get_db
from app.schemas.api_response import ApiResponse
from app.schemas.category_schema import (
    CategoryResponse,
    CreateCategoryRequest,
    UpdateCategoryRequest,
)
from app.services.category_service import CategoryService

router = APIRouter(prefix="/category", tags=["category"])

category_service = CategoryService()


@router.post("", response_model=ApiResponse[CategoryResponse])
def create_category(
    request: CreateCategoryRequest,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    category = category_service.create(db, request)
    return ApiResponse(success=True, message="Category created successfully", data=category)


@router.get("", response_model=ApiResponse[list[CategoryResponse]])
def list_categories(db: Session = Depends(get_db)):
    """Public listing of categories"""
    categories = category_service.get_all(db)
    return ApiResponse(success=True, message="Categories fetched successfully", data=categories)


@router.get("/{category_id}", response_model=ApiResponse[CategoryResponse])
def get_category(category_id: int, db: Session = Depends(get_db)):
    category = category_service.get_by_id(db, category_id)
    return ApiResponse(success=True, message="Category fetched successfully", data=category)


@router.put("/{category_id}", response_model=ApiResponse[CategoryResponse])
def update_category(
    category_id: int,
    request: UpdateCategoryRequest,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    category = category_service.update(db, category_id, request)
    return ApiResponse(success=True, message="Category updated successfully", data=category)


@router.delete("/{category_id}", response_model=ApiResponse[CategoryResponse])
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    _current_user: CurrentUser = Depends(require_roles("admin", "staff")),
):
    category = category_service.delete(db, category_id)
    return ApiResponse(success=True, message="Category deleted successfully", data=category)
