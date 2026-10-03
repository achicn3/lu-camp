"""組合包袋裝條碼：bundle_packs（一袋一張條碼，掛在組合價活動上）＋bundle_pack_items（袋裡裝什麼）。

ADR-028（店主 2026-10-04）：掃袋裝條碼＝把袋裡的商品加進購物車，價錢照所屬組合價活動算。
降版時若已建過袋子就拒絕（印出去的標籤會掃不到）。

Revision ID: c4e8a2f6b1d3
Revises: 7b2e4f19c0a5
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "c4e8a2f6b1d3"
down_revision = "7b2e4f19c0a5"
branch_labels = None
depends_on = None

_ITEM_TYPES = ("SERIALIZED", "CATALOG", "BULK_BASKET")


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "bundle_packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("code", name="uq_bundle_packs_code"),
    )
    op.create_index("ix_bundle_packs_store_id", "bundle_packs", ["store_id"])
    op.create_index("ix_bundle_packs_campaign_id", "bundle_packs", ["campaign_id"])
    op.create_table(
        "bundle_pack_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("pack_id", sa.Integer(), sa.ForeignKey("bundle_packs.id"), nullable=False),
        sa.Column(
            "item_type",
            sa.Enum(
                *_ITEM_TYPES,
                name="bundlepackitemtype",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("serialized_item_id", sa.Integer(), sa.ForeignKey("serialized_items.id")),
        sa.Column("catalog_product_id", sa.Integer(), sa.ForeignKey("catalog_products.id")),
        sa.Column("bulk_basket_id", sa.Integer(), sa.ForeignKey("bulk_baskets.id")),
        sa.Column("qty", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("qty BETWEEN 1 AND 99", name="ck_bundle_pack_items_qty"),
        sa.CheckConstraint(
            "(item_type = 'SERIALIZED' AND serialized_item_id IS NOT NULL AND qty = 1"
            " AND catalog_product_id IS NULL AND bulk_basket_id IS NULL)"
            " OR (item_type = 'CATALOG' AND catalog_product_id IS NOT NULL"
            " AND serialized_item_id IS NULL AND bulk_basket_id IS NULL)"
            " OR (item_type = 'BULK_BASKET' AND bulk_basket_id IS NOT NULL"
            " AND serialized_item_id IS NULL AND catalog_product_id IS NULL)",
            name="ck_bundle_pack_items_target",
        ),
    )
    op.create_index("ix_bundle_pack_items_store_id", "bundle_pack_items", ["store_id"])
    op.create_index("ix_bundle_pack_items_pack_id", "bundle_pack_items", ["pack_id"])


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM bundle_packs")).scalar_one()
    if count:
        raise RuntimeError(
            f"已有 {count} 個組合包袋裝條碼，降版會讓印出去的標籤掃不到；只退程式、不要降資料庫"
        )
    op.drop_table("bundle_pack_items")
    op.drop_table("bundle_packs")
