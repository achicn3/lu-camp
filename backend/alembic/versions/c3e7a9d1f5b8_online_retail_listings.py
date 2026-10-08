"""帶回家零售商品上線與交貨（docs/63 §13；M1d；店主 2026-10-08）。

- 新表 online_retail_listings：既有一般商品的線上呈現（介紹、加購角色、照片、啟用、排序），
  不存價格／庫存。
- online_orders 加 fulfillment_status／handed_over_at／handed_over_by：付款與交貨分開。
- stock_movements.reason 加 ONLINE_HOLD／ONLINE_RELEASE：線上單保留帶回家商品（先扣、之後加回）。

Revision ID: c3e7a9d1f5b8
Revises: b7d4f1a3c9e2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "c3e7a9d1f5b8"
down_revision = "b7d4f1a3c9e2"
branch_labels = None
depends_on = None

_STOCK_REASON_CK = "stockreason"
_OLD_REASONS = (
    "ACQUISITION",
    "PURCHASE",
    "SALE",
    "RETURN",
    "CONSIGN_RETURN",
    "GIFT",
    "GIFT_RETURN",
    "WRITE_OFF",
    "STOCKTAKE",
)
_NEW_REASONS = (*_OLD_REASONS, "ONLINE_HOLD", "ONLINE_RELEASE")


def _reasons(values: tuple[str, ...]) -> None:
    op.drop_constraint(_STOCK_REASON_CK, "stock_movements", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        _STOCK_REASON_CK, "stock_movements", sa.text(f"reason IN ({allowed})")
    )


def upgrade() -> None:
    op.create_table(
        "online_retail_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "catalog_product_id",
            sa.Integer(),
            sa.ForeignKey("catalog_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "coffee",
                "dessert",
                "experience",
                "bean",
                "drip",
                "other",
                name="menuupsellrole",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("photo_sha256", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "store_id", "catalog_product_id", name="uq_online_retail_listings_product"
        ),
        sa.CheckConstraint("sort_order BETWEEN 0 AND 9999", name="ck_online_retail_listings_sort"),
        sa.ForeignKeyConstraint(
            ["store_id", "photo_sha256"],
            ["menu_photos.store_id", "menu_photos.sha256"],
            name="fk_online_retail_listings_photo",
        ),
    )
    op.create_index("ix_online_retail_listings_store_id", "online_retail_listings", ["store_id"])
    op.create_index(
        "ix_online_retail_listings_catalog_product_id",
        "online_retail_listings",
        ["catalog_product_id"],
    )
    op.add_column(
        "online_orders",
        sa.Column(
            "fulfillment_status", sa.String(12), nullable=False, server_default=sa.text("'NONE'")
        ),
    )
    op.add_column(
        "online_orders", sa.Column("handed_over_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "online_orders",
        sa.Column("handed_over_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_check_constraint(
        "ck_online_orders_fulfillment",
        "online_orders",
        "fulfillment_status IN ('NONE', 'AWAITING', 'HANDED_OVER')",
    )
    op.create_check_constraint(
        "ck_online_orders_handed_over",
        "online_orders",
        "fulfillment_status <> 'HANDED_OVER' OR "
        "(sync_status = 'SETTLED' AND handed_over_at IS NOT NULL AND handed_over_by IS NOT NULL)",
    )
    _reasons(_NEW_REASONS)


def abort_if_online_retail_data_exists(conn: Connection) -> None:
    """降版會刪掉帶回家商品設定與交貨紀錄、舊程式也認不得保留異動；已有資料就中止。"""
    listings = conn.execute(sa.text("SELECT count(*) FROM online_retail_listings")).scalar_one()
    holds = conn.execute(
        sa.text(
            "SELECT count(*) FROM stock_movements WHERE reason IN ('ONLINE_HOLD', 'ONLINE_RELEASE')"
        )
    ).scalar_one()
    fulfilled = conn.execute(
        sa.text("SELECT count(*) FROM online_orders WHERE fulfillment_status <> 'NONE'")
    ).scalar_one()
    if listings or holds or fulfilled:
        raise RuntimeError(
            f"拒絕降版：已有 {listings} 個帶回家商品、{holds} 筆線上保留異動、"
            f"{fulfilled} 張要交貨的線上單；只退程式、不要降資料庫"
        )


def downgrade() -> None:
    abort_if_online_retail_data_exists(op.get_bind())
    _reasons(_OLD_REASONS)
    op.drop_constraint("ck_online_orders_handed_over", "online_orders", type_="check")
    op.drop_constraint("ck_online_orders_fulfillment", "online_orders", type_="check")
    op.drop_column("online_orders", "handed_over_by")
    op.drop_column("online_orders", "handed_over_at")
    op.drop_column("online_orders", "fulfillment_status")
    op.drop_index("ix_online_retail_listings_catalog_product_id", "online_retail_listings")
    op.drop_index("ix_online_retail_listings_store_id", "online_retail_listings")
    op.drop_table("online_retail_listings")
