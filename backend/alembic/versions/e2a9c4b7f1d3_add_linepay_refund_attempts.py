"""linepay_refund_attempts 退款嘗試對帳日誌（docs/30 finding #1：防重退）

append-only、無外鍵，以獨立交易提交（跨主交易回滾存活）。refund_key 唯一——退貨/作廢各只退
一次；重試前查狀態：SUCCEEDED 跳過、PENDING 結果未定 fail-closed、FAILED 可重試。
status 以 _enum_col native_enum=False → CHECK（名 linepayrefundstatus）。

Revision ID: e2a9c4b7f1d3
Revises: a7f3c1e9d2b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a9c4b7f1d3"
down_revision: str | None = "a7f3c1e9d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_VALUES = ("PENDING", "SUCCEEDED", "FAILED")


def upgrade() -> None:
    allowed = ", ".join(f"'{v}'" for v in _STATUS_VALUES)
    op.create_table(
        "linepay_refund_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("refund_key", sa.String(120), nullable=False),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 0), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("return_code", sa.String(8), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("refund_key", name="uq_linepay_refund_attempts_key"),
        sa.CheckConstraint("amount > 0", name="ck_linepay_refund_attempts_amount_positive"),
        sa.CheckConstraint(f"status IN ({allowed})", name="linepayrefundstatus"),
    )
    op.create_index(
        "ix_linepay_refund_attempts_store_id", "linepay_refund_attempts", ["store_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_linepay_refund_attempts_store_id", table_name="linepay_refund_attempts"
    )
    op.drop_table("linepay_refund_attempts")
