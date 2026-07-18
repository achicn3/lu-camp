"""linepay_transactions 交易紀錄表（docs/30 P2）

LINE Pay 收款的對帳/退款/稽核紀錄。一筆 LINE_PAY 收款的銷售對應一列：
- order_id 唯一（由 (store, 冪等鍵) 確定性導出；重試恆同號、先 check 防重複扣款）。
- sale_id 唯一（一銷售至多一筆 LINE Pay 交易）；複合租戶 FK (sale_id, store_id)→sales。
- transaction_id 平台 64-bit 長整數以字串保存（避免 JS/JSON Number 失真）。
- status 以 _enum_col native_enum=False → CHECK（名 linepaystatus）。
- refunded_amount ∈ [0, amount]，amount > 0。raw_response JSONB 存 pay 原始回應。

Revision ID: a7f3c1e9d2b4
Revises: c7d8e9f0a1b2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7f3c1e9d2b4"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_VALUES = ("COMPLETE", "FAILED", "REFUNDED", "VOIDED")


def upgrade() -> None:
    allowed = ", ".join(f"'{v}'" for v in _STATUS_VALUES)
    op.create_table(
        "linepay_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("transaction_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 0), nullable=False),
        sa.Column(
            "refunded_amount",
            sa.Numeric(12, 0),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("raw_response", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_linepay_transactions_sale_tenant",
        ),
        sa.UniqueConstraint("order_id", name="uq_linepay_transactions_order_id"),
        sa.UniqueConstraint("sale_id", name="uq_linepay_transactions_sale_id"),
        sa.CheckConstraint("amount > 0", name="ck_linepay_transactions_amount_positive"),
        sa.CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name="ck_linepay_transactions_refund_bounds",
        ),
        sa.CheckConstraint(f"status IN ({allowed})", name="linepaystatus"),
    )
    op.create_index(
        "ix_linepay_transactions_store_id", "linepay_transactions", ["store_id"]
    )
    op.create_index(
        "ix_linepay_transactions_sale_id", "linepay_transactions", ["sale_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_linepay_transactions_sale_id", table_name="linepay_transactions")
    op.drop_index("ix_linepay_transactions_store_id", table_name="linepay_transactions")
    op.drop_table("linepay_transactions")
