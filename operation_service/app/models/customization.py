from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class CustomizationGroup(Base):
    __tablename__ = "customization_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    selection_type: Mapped[str] = mapped_column(String(20), default="single")  # 'single' or 'multiple'

    options: Mapped[list["CustomizationOption"]] = relationship(
        "CustomizationOption", back_populates="group", cascade="all, delete-orphan"
    )


class CustomizationOption(Base):
    __tablename__ = "customization_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("customization_groups.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    extra_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)

    group: Mapped["CustomizationGroup"] = relationship("CustomizationGroup", back_populates="options")
