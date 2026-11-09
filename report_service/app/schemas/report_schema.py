from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class SalesSummaryResponse(BaseModel):
    start_date: date
    end_date: date
    total_orders: int
    paid_orders: int
    cancelled_orders: int
    total_revenue: Decimal  # sum of total_amount for paid/completed orders
    average_order_value: Decimal


class DailySalesPoint(BaseModel):
    day: date
    order_count: int
    revenue: Decimal


class TopProductResponse(BaseModel):
    product_id: int
    product_name: str
    quantity_sold: int
    revenue: Decimal


class UserSummaryResponse(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    new_users_in_range: int
    users_by_role: dict[str, int]


class InventoryAlertResponse(BaseModel):
    product_id: int
    product_name: str
    stock_quantity: int


class SyncResultResponse(BaseModel):
    users_synced: int
    products_synced: int
    orders_synced: int
