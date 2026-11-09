from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=255)


class CreateCategoryRequest(CategoryBase):
    pass


class UpdateCategoryRequest(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
