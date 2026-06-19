"""add stocktake

Revision ID: a7f3c1d9e2b4
Revises: 7d9e2c4a1b8f
Create Date: 2026-06-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f3c1d9e2b4"
down_revision: str | Sequence[str] | None = "7d9e2c4a1b8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum_col(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, length=30, create_constraint=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "stocktakes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum_col("DRAFT", "CONFIRMED"),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stocktakes_store_id"), "stocktakes", ["store_id"], unique=False)

    op.create_table(
        "stocktake_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("stocktake_id", sa.Integer(), nullable=False),
        sa.Column("catalog_product_id", sa.Integer(), nullable=False),
        sa.Column("system_qty", sa.Integer(), nullable=False),
        sa.Column("counted_qty", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["stocktake_id"], ["stocktakes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_product_id"], ["catalog_products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_stocktake_lines_store_id"), "stocktake_lines", ["store_id"], unique=False
    )
    op.create_index(
        op.f("ix_stocktake_lines_stocktake_id"),
        "stocktake_lines",
        ["stocktake_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stocktake_lines_catalog_product_id"),
        "stocktake_lines",
        ["catalog_product_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("stocktake_lines")
    op.drop_table("stocktakes")
