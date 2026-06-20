"""add returns

Revision ID: b8e4f9a2c6d1
Revises: a7f3c1d9e2b4
Create Date: 2026-06-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4f9a2c6d1"
down_revision: str | Sequence[str] | None = "a7f3c1d9e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CASH_TYPES_OLD = (
    "SALE_IN",
    "BUYOUT_OUT",
    "CONSIGNMENT_PAYOUT_OUT",
    "MANUAL_ADJUST",
    "ACQUISITION_VOID_IN",
)
_CASH_TYPES_NEW = (*_CASH_TYPES_OLD, "SALE_REFUND_OUT")


def _check_clause(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"(type)::text = ANY (ARRAY[{joined}]::text[])"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("cashmovementtype", "cash_movements", type_="check")
    op.create_check_constraint("cashmovementtype", "cash_movements", _check_clause(_CASH_TYPES_NEW))

    # 複合租戶錨點：供 return_lines 的 (sale_line_id, store_id) 複合 FK 綁定。
    op.create_unique_constraint("uq_sale_lines_id_store", "sale_lines", ["id", "store_id"])

    op.create_table(
        "returns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 0), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("clerk_user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_returns_sale_store",
        ),
        sa.ForeignKeyConstraint(["clerk_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "store_id", name="uq_returns_id_store"),
        sa.UniqueConstraint("store_id", "idempotency_key", name="uq_returns_store_idempotency_key"),
    )
    op.create_index(op.f("ix_returns_store_id"), "returns", ["store_id"], unique=False)
    op.create_index(op.f("ix_returns_sale_id"), "returns", ["sale_id"], unique=False)

    op.create_table(
        "return_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("return_id", sa.Integer(), nullable=False),
        sa.Column("sale_line_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("refund_amount", sa.Numeric(12, 0), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(
            ["return_id", "store_id"],
            ["returns.id", "returns.store_id"],
            ondelete="CASCADE",
            name="fk_return_lines_return_store",
        ),
        sa.ForeignKeyConstraint(
            ["sale_line_id", "store_id"],
            ["sale_lines.id", "sale_lines.store_id"],
            name="fk_return_lines_sale_line_store",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_return_lines_store_id"), "return_lines", ["store_id"], unique=False)
    op.create_index(op.f("ix_return_lines_return_id"), "return_lines", ["return_id"], unique=False)
    op.create_index(
        op.f("ix_return_lines_sale_line_id"), "return_lines", ["sale_line_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("return_lines")
    op.drop_table("returns")
    op.drop_constraint("uq_sale_lines_id_store", "sale_lines", type_="unique")

    op.drop_constraint("cashmovementtype", "cash_movements", type_="check")
    op.create_check_constraint("cashmovementtype", "cash_movements", _check_clause(_CASH_TYPES_OLD))
