from typing import Generic, TypeVar, Optional

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """
    Standard response envelope used across every endpoint in this service.

    NOTE: pydantic v2 removed `pydantic.generics.GenericModel` (it was a v1-only
    shim). In v2, `BaseModel` itself supports `Generic[T]` directly, which is
    what makes `ApiResponse[UserResponse]` work correctly as a response_model.
    """

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
