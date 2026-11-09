"""Add product_images table for product image upload metadata.

Revision ID: 0002_product_images
Revises: 0001_bakong_khqr
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_product_images"
down_revision: Union[str, None] = "0001_bakong_khqr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "product_images" not in existing_tables:
        op.create_table(
            "product_images",
            sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column(
                "product_id",
                sa.Integer(),
                sa.ForeignKey("products.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("storage_key", sa.String(length=128), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=True),
            sa.Column("content_type", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("alt_text", sa.String(length=255), nullable=True),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_product_images_product_id", "product_images", ["product_id"], unique=False)
        op.create_index("ix_product_images_storage_key", "product_images", ["storage_key"], unique=True)
        op.create_index(
            "ix_product_images_one_primary_per_product",
            "product_images",
            ["product_id"],
            unique=True,
            postgresql_where=sa.text("is_primary = true"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "product_images" in existing_tables:
        op.drop_index("ix_product_images_one_primary_per_product", table_name="product_images")
        op.drop_index("ix_product_images_storage_key", table_name="product_images")
        op.drop_index("ix_product_images_product_id", table_name="product_images")
        op.drop_table("product_images")
