from datetime import date

from sqlalchemy.orm import Session

from app.core.exception import InvalidDateRange
from app.repository import sales_report_repository as sales_repo
from app.repository import user_report_repository as user_repo
from app.schemas.report_schema import (
    DailySalesPoint,
    InventoryAlertResponse,
    SalesSummaryResponse,
    TopProductResponse,
    UserSummaryResponse,
)


class ReportService:
    @staticmethod
    def _validate_range(start_date: date, end_date: date):
        if start_date > end_date:
            raise InvalidDateRange()

    # ----------------------------------------------------------- sales
    def sales_summary(self, db: Session, start_date: date, end_date: date) -> SalesSummaryResponse:
        self._validate_range(start_date, end_date)

        total_orders = sales_repo.count_orders_in_range(db, start_date, end_date)
        paid_orders = sales_repo.count_orders_by_status_in_range(db, start_date, end_date, "paid")
        completed_orders = sales_repo.count_orders_by_status_in_range(db, start_date, end_date, "completed")
        cancelled_orders = sales_repo.count_orders_by_status_in_range(db, start_date, end_date, "cancelled")
        total_revenue = sales_repo.total_revenue_in_range(db, start_date, end_date)

        revenue_order_count = paid_orders + completed_orders
        average_order_value = (total_revenue / revenue_order_count) if revenue_order_count else 0

        return SalesSummaryResponse(
            start_date=start_date,
            end_date=end_date,
            total_orders=total_orders,
            paid_orders=revenue_order_count,
            cancelled_orders=cancelled_orders,
            total_revenue=round(total_revenue, 2),
            average_order_value=round(average_order_value, 2),
        )

    def daily_sales(self, db: Session, start_date: date, end_date: date) -> list[DailySalesPoint]:
        self._validate_range(start_date, end_date)
        rows = sales_repo.daily_sales_in_range(db, start_date, end_date)
        return [DailySalesPoint(day=row[0], order_count=row[1], revenue=round(float(row[2]), 2)) for row in rows]

    def top_products(self, db: Session, start_date: date, end_date: date, limit: int = 10) -> list[TopProductResponse]:
        self._validate_range(start_date, end_date)
        rows = sales_repo.top_products_in_range(db, start_date, end_date, limit=limit)
        return [
            TopProductResponse(
                product_id=row[0], product_name=row[1], quantity_sold=int(row[2]), revenue=round(float(row[3]), 2)
            )
            for row in rows
        ]

    def low_stock_alerts(self, db: Session, threshold: int = 10) -> list[InventoryAlertResponse]:
        products = sales_repo.low_stock_products(db, threshold=threshold)
        return [
            InventoryAlertResponse(product_id=p.product_id, product_name=p.name, stock_quantity=p.stock_quantity)
            for p in products
        ]

    # ------------------------------------------------------------ users
    def user_summary(self, db: Session, start_date: date, end_date: date) -> UserSummaryResponse:
        self._validate_range(start_date, end_date)

        total_users = user_repo.count_total(db)
        active_users = user_repo.count_active(db)
        new_users = user_repo.count_new_in_range(db, start_date, end_date)
        by_role = user_repo.count_by_role(db)

        return UserSummaryResponse(
            total_users=total_users,
            active_users=active_users,
            inactive_users=total_users - active_users,
            new_users_in_range=new_users,
            users_by_role=by_role,
        )
