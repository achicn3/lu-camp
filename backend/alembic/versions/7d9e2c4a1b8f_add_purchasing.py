"""add purchasing

Revision ID: 7d9e2c4a1b8f
Revises: e8c70a9e0447
Create Date: 2026-06-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d9e2c4a1b8f"
down_revision: str | Sequence[str] | None = "e8c70a9e0447"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum_col(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, length=30, create_constraint=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("contact", sa.String(length=200), nullable=True),
        sa.Column("tax_id", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "name", name="uq_suppliers_store_name"),
    )
    op.create_index(op.f("ix_suppliers_store_id"), "suppliers", ["store_id"], unique=False)

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum_col("DRAFT", "ORDERED", "RECEIVED", "CLOSED"),
            server_default="ORDERED",
            nullable=False,
        ),
        sa.Column("ordered_by", sa.Integer(), nullable=False),
        sa.Column(
            "ordered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["ordered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["received_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_purchase_orders_store_id"), "purchase_orders", ["store_id"], unique=False
    )
    op.create_index(
        op.f("ix_purchase_orders_supplier_id"), "purchase_orders", ["supplier_id"], unique=False
    )

    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("catalog_product_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 0), nullable=False),
        sa.ForeignKeyConstraint(["catalog_product_id"], ["catalog_products.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_purchase_order_lines_catalog_product_id"),
        "purchase_order_lines",
        ["catalog_product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_order_lines_purchase_order_id"),
        "purchase_order_lines",
        ["purchase_order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_purchase_order_lines_store_id"),
        "purchase_order_lines",
        ["store_id"],
        unique=False,
    )

    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("received_by", sa.Integer(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
        sa.ForeignKeyConstraint(["received_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("purchase_order_id", name="uq_goods_receipts_purchase_order_id"),
    )
    op.create_index(
        op.f("ix_goods_receipts_purchase_order_id"),
        "goods_receipts",
        ["purchase_order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_goods_receipts_store_id"), "goods_receipts", ["store_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_goods_receipts_store_id"), table_name="goods_receipts")
    op.drop_index(op.f("ix_goods_receipts_purchase_order_id"), table_name="goods_receipts")
    op.drop_table("goods_receipts")
    op.drop_index(op.f("ix_purchase_order_lines_store_id"), table_name="purchase_order_lines")
    op.drop_index(
        op.f("ix_purchase_order_lines_purchase_order_id"), table_name="purchase_order_lines"
    )
    op.drop_index(
        op.f("ix_purchase_order_lines_catalog_product_id"), table_name="purchase_order_lines"
    )
    op.drop_table("purchase_order_lines")
    op.drop_index(op.f("ix_purchase_orders_supplier_id"), table_name="purchase_orders")
    op.drop_index(op.f("ix_purchase_orders_store_id"), table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.drop_index(op.f("ix_suppliers_store_id"), table_name="suppliers")
    op.drop_table("suppliers")
