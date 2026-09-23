"""門市活動 v2（docs/40 P1a）：可同時多個生效、可疊加、範圍到指定商品；每行套用明細。

- campaigns：拿掉「同店至多一個 ACTIVE」的 partial unique；
  加 stackable（預設 false＝不跟任何活動併用）。
- campaign_targets：包含／排除條件（分類、品牌、型號、單件、一般商品、販售籃）。
- sale_line_campaigns：一行套到哪些活動、各折多少；**回填**舊單
  （sale_lines.campaign_id＋discount_amount），活動成效報表改讀這張表後舊資料仍在。

降版：刪新表與 stackable、恢復 partial unique。若降版時同店已有多個 ACTIVE，重建唯一索引會失敗
——那是刻意的：舊程式假設只有一個，請先結束多餘的活動再降版。
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "a3d7e1f9c2b4"
down_revision = "f2c8a4d61e93"
branch_labels = None
depends_on = None

_TARGET_MODES = ("INCLUDE", "EXCLUDE")
_TARGET_TYPES = (
    "CATEGORY",
    "BRAND",
    "PRODUCT_MODEL",
    "SERIALIZED_ITEM",
    "CATALOG_PRODUCT",
    "BULK_BASKET",
)


def backfill_sale_line_campaigns(conn: Connection) -> None:
    """舊單：一行至多一個活動，折讓就是 sale_lines.discount_amount。已有明細的行跳過（可重跑）。"""
    conn.execute(
        sa.text(
            "INSERT INTO sale_line_campaigns (store_id, sale_line_id, campaign_id, discount_amount)"
            " SELECT sl.store_id, sl.id, sl.campaign_id, sl.discount_amount FROM sale_lines sl"
            " WHERE sl.campaign_id IS NOT NULL AND sl.discount_amount > 0"
            " AND NOT EXISTS (SELECT 1 FROM sale_line_campaigns slc WHERE slc.sale_line_id = sl.id)"
        )
    )


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.drop_index("uq_one_active_campaign_per_store", table_name="campaigns")
    op.add_column(
        "campaigns",
        sa.Column("stackable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "campaign_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column(
            "mode",
            sa.Enum(
                *_TARGET_MODES,
                name="campaigntargetmode",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "target_type",
            sa.Enum(
                *_TARGET_TYPES,
                name="campaigntargettype",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "campaign_id", "mode", "target_type", "target_id", name="uq_campaign_targets_entry"
        ),
    )
    op.create_index("ix_campaign_targets_store_id", "campaign_targets", ["store_id"])
    op.create_index("ix_campaign_targets_campaign_id", "campaign_targets", ["campaign_id"])

    op.create_table(
        "sale_line_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("discount_amount", sa.Numeric(12, 0), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("sale_line_id", "campaign_id", name="uq_sale_line_campaigns_entry"),
        sa.CheckConstraint("discount_amount > 0", name="ck_sale_line_campaigns_amount_pos"),
    )
    for column in ("store_id", "sale_line_id", "campaign_id"):
        op.create_index(f"ix_sale_line_campaigns_{column}", "sale_line_campaigns", [column])
    backfill_sale_line_campaigns(op.get_bind())


def downgrade() -> None:
    op.drop_table("sale_line_campaigns")
    op.drop_table("campaign_targets")
    op.drop_column("campaigns", "stackable")
    op.create_index(
        "uq_one_active_campaign_per_store",
        "campaigns",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
