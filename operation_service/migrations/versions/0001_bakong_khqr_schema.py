"""Add Bakong KHQR payment tables and columns.

Revision ID: 0001_bakong_khqr
Revises: None
Create Date: 2026-11-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_bakong_khqr"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # 1. Extend existing `payments` table with Bakong KHQR columns if not already present
    if "payments" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("payments")}
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("payments")}
        new_cols = [
            ("currency", sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD")),
            ("qr_string", sa.Column("qr_string", sa.Text(), nullable=True)),
            ("qr_md5", sa.Column("qr_md5", sa.String(length=32), nullable=True)),
            ("deeplink_url", sa.Column("deeplink_url", sa.String(length=512), nullable=True)),
            ("expires_at", sa.Column("expires_at", sa.DateTime(), nullable=True)),
            ("paid_at", sa.Column("paid_at", sa.DateTime(), nullable=True)),
            ("bakong_transaction_hash", sa.Column("bakong_transaction_hash", sa.String(length=128), nullable=True)),
            ("payer_account_masked", sa.Column("payer_account_masked", sa.String(length=64), nullable=True)),
            ("receiver_account", sa.Column("receiver_account", sa.String(length=64), nullable=True)),
            ("attempt_count", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1")),
        ]
        cols_to_add = [col for name, col in new_cols if name not in existing_cols]
        if cols_to_add:
            with op.batch_alter_table("payments") as batch_op:
                for col in cols_to_add:
                    batch_op.add_column(col)
                if "ix_payments_qr_md5" not in existing_indexes:
                    batch_op.create_index("ix_payments_qr_md5", ["qr_md5"], unique=True)
                if "ix_payments_bakong_tx_hash" not in existing_indexes:
                    batch_op.create_index("ix_payments_bakong_tx_hash", ["bakong_transaction_hash"], unique=True)

    # 2. `payment_attempts` table
    if "payment_attempts" not in existing_tables:
        op.create_table(
            "payment_attempts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
            sa.Column("qr_string", sa.Text(), nullable=False),
            sa.Column("qr_md5", sa.String(length=32), nullable=False),
            sa.Column("deeplink_url", sa.String(length=512), nullable=True),
            sa.Column("bill_number", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("paid_at", sa.DateTime(), nullable=True),
            sa.Column("bakong_transaction_hash", sa.String(length=128), nullable=True),
            sa.Column("payer_account_masked", sa.String(length=64), nullable=True),
            sa.Column("raw_bakong_response", sa.Text(), nullable=True),
            sa.Column("failure_reason", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("payment_id", "attempt_number", name="uq_payment_attempt_number"),
            sa.UniqueConstraint("qr_md5", name="uq_payment_attempts_qr_md5"),
            sa.UniqueConstraint("bakong_transaction_hash", name="uq_payment_attempts_tx_hash"),
        )
        op.create_index("ix_payment_attempts_order_id", "payment_attempts", ["order_id"])

    # 3. `payment_events` immutable audit log table
    if "payment_events" not in existing_tables:
        op.create_table(
            "payment_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("previous_status", sa.String(length=24), nullable=True),
            sa.Column("new_status", sa.String(length=24), nullable=True),
            sa.Column("qr_md5", sa.String(length=32), nullable=True),
            sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"])
        op.create_index("ix_payment_events_order_id", "payment_events", ["order_id"])

    # 4. `refunds` table
    if "refunds" not in existing_tables:
        op.create_table(
            "refunds",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
            sa.Column("reason", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_manual"),
            sa.Column("external_refund_ref", sa.String(length=128), nullable=True),
            sa.Column("processed_by_user_id", sa.Integer(), nullable=True),
            sa.Column("refunded_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_refunds_order_id", "refunds", ["order_id"])

    # 5. `idempotency_keys` table
    if "idempotency_keys" not in existing_tables:
        op.create_table(
            "idempotency_keys",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("endpoint", sa.String(length=128), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("response_status", sa.Integer(), nullable=False),
            sa.Column("response_body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("idempotency_key", "user_id", "endpoint", name="uq_idempotency_key_user_endpoint"),
        )

    # 6. `verification_jobs` queue table for PostgreSQL FOR UPDATE SKIP LOCKED worker
    if "verification_jobs" not in existing_tables:
        op.create_table(
            "verification_jobs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "attempt_id",
                sa.Integer(),
                sa.ForeignKey("payment_attempts.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("qr_md5", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="40"),
            sa.Column("next_run_at", sa.DateTime(), nullable=False),
            sa.Column("locked_at", sa.DateTime(), nullable=True),
            sa.Column("locked_by", sa.String(length=64), nullable=True),
            sa.Column("last_error", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("qr_md5", name="uq_verification_jobs_qr_md5"),
        )
        op.create_index("ix_verification_jobs_status_next_run", "verification_jobs", ["status", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_verification_jobs_status_next_run", table_name="verification_jobs")
    op.drop_table("verification_jobs")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_refunds_order_id", table_name="refunds")
    op.drop_table("refunds")
    op.drop_index("ix_payment_events_order_id", table_name="payment_events")
    op.drop_index("ix_payment_events_payment_id", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index("ix_payment_attempts_order_id", table_name="payment_attempts")
    op.drop_table("payment_attempts")

    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_index("ix_payments_bakong_tx_hash")
        batch_op.drop_index("ix_payments_qr_md5")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("receiver_account")
        batch_op.drop_column("payer_account_masked")
        batch_op.drop_column("bakong_transaction_hash")
        batch_op.drop_column("paid_at")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("deeplink_url")
        batch_op.drop_column("qr_md5")
        batch_op.drop_column("qr_string")
        batch_op.drop_column("currency")
