from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderItemCustomizationRequest(BaseModel):
    group_id: int
    option_id: int


class OrderItemRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    customizations: list[OrderItemCustomizationRequest] = []


class CreateOrderRequest(BaseModel):
    items: list[OrderItemRequest] = Field(min_length=1)


class UpdateOrderStatusRequest(BaseModel):
    status: str = Field(pattern="^(pending|paid|cancelled|completed)$")


class OrderItemCustomizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    group_name: str
    option_id: int
    option_name: str
    extra_price: Decimal


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str
    product_image_url: str | None = None
    product_image_alt: str | None = None
    unit_price: Decimal
    quantity: int
    subtotal: Decimal
    customizations: list[OrderItemCustomizationResponse] = []


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    status: str
    total_amount: Decimal
    created_at: datetime
    items: list[OrderItemResponse]
