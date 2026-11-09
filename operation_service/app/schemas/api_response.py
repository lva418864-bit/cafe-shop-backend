from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    error_code: Optional[str] = None
    message: str
    data: Optional[T] = None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta
