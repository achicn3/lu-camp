"""散裝販售籃（ADR-025）：bulk_baskets、bulk_lots.basket_id、販售籃行與來源分配。"""

import sqlalchemy as sa
from alembic import op

revision = "e7b3c9d15a42"
down_revision = "d4e9b2a61c08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bulk_baskets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=True),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("unit_price", sa.Numeric(12, 0), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "store_id", name="uq_bulk_baskets_id_store"),
        sa.CheckConstraint("unit_price >= 0", name="ck_bulk_baskets_unit_price_nonneg"),
    )
    op.create_index("ix_bulk_baskets_store_id", "bulk_baskets", ["store_id"])
    op.create_index("ix_bulk_baskets_code", "bulk_baskets", ["code"], unique=True)
    op.add_column(
        "bulk_lots",
        sa.Column("basket_id", sa.Integer(), sa.ForeignKey("bulk_baskets.id"), nullable=True),
    )
    op.create_index("ix_bulk_lots_basket_id", "bulk_lots", ["basket_id"])
    op.add_column(
        "sale_lines",
        sa.Column("bulk_basket_id", sa.Integer(), sa.ForeignKey("bulk_baskets.id"), nullable=True),
    )
    op.create_table(
        "sale_bulk_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id"), nullable=False),
        sa.Column("bulk_lot_id", sa.Integer(), sa.ForeignKey("bulk_lots.id"), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("cost_snapshot", sa.Numeric(12, 0), nullable=False),
        sa.Column("returned_qty", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("qty > 0", name="ck_sale_bulk_allocations_qty_pos"),
        sa.CheckConstraint(
            "returned_qty >= 0 AND returned_qty <= qty",
            name="ck_sale_bulk_allocations_returned_range",
        ),
        sa.CheckConstraint("cost_snapshot >= 0", name="ck_sale_bulk_allocations_cost_nonneg"),
    )
    for column in ("store_id", "sale_line_id", "bulk_lot_id"):
        op.create_index(f"ix_sale_bulk_allocations_{column}", "sale_bulk_allocations", [column])


def downgrade() -> None:
    for column in ("store_id", "sale_line_id", "bulk_lot_id"):
        op.drop_index(f"ix_sale_bulk_allocations_{column}", table_name="sale_bulk_allocations")
    op.drop_table("sale_bulk_allocations")
    op.drop_column("sale_lines", "bulk_basket_id")
    op.drop_index("ix_bulk_lots_basket_id", table_name="bulk_lots")
    op.drop_column("bulk_lots", "basket_id")
    op.drop_index("ix_bulk_baskets_code", table_name="bulk_baskets")
    op.drop_index("ix_bulk_baskets_store_id", table_name="bulk_baskets")
    op.drop_table("bulk_baskets")
