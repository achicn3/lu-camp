"""門市活動 v2 P4（docs/40 §4）：組合價。

- campaigns.kind 加 BUNDLE、新欄 bundle_price；「類型與數值一致」檢查加一種（不可開寄售，裁示 7）。
- campaign_bundle_slots（格子：件數）＋campaign_bundle_slot_targets（格子的範圍，只有包含）。
- sale_bundle_groups（成交的一組）＋sale_bundle_members（哪一行幾件在組內）；整組退時記退貨單。

降版：若已有組合價活動或成交紀錄，舊檢查會擋下／資料會遺失——請先作廢活動再降版。
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "e5b3d8a1c7f2"
down_revision = "d2a7c5e9f4b1"
branch_labels = None
depends_on = None

_OLD_KINDS = ("PERCENT_OFF", "FIXED_PRICE", "AMOUNT_OFF", "BUY_N_GET_M")
_NEW_KINDS = (*_OLD_KINDS, "BUNDLE")

_TARGET_TYPES = (
    "CATEGORY",
    "BRAND",
    "PRODUCT_MODEL",
    "SERIALIZED_ITEM",
    "CATALOG_PRODUCT",
    "BULK_BASKET",
)

_NO_BNGM = " AND buy_qty IS NULL AND free_qty IS NULL"
_OLD_KIND_VALUE_CHECK = (
    "(kind = 'PERCENT_OFF' AND discount_pct BETWEEN 1 AND 99"
    f" AND fixed_price IS NULL AND amount_off IS NULL{_NO_BNGM})"
    " OR (kind = 'FIXED_PRICE' AND fixed_price > 0"
    f" AND discount_pct IS NULL AND amount_off IS NULL{_NO_BNGM})"
    " OR (kind = 'AMOUNT_OFF' AND amount_off > 0"
    f" AND discount_pct IS NULL AND fixed_price IS NULL{_NO_BNGM})"
    " OR (kind = 'BUY_N_GET_M' AND buy_qty BETWEEN 1 AND 99 AND free_qty BETWEEN 1 AND 99"
    " AND discount_pct IS NULL AND fixed_price IS NULL AND amount_off IS NULL"
    " AND NOT applies_consignment)"
)
_NO_OTHERS = f"{_NO_BNGM} AND bundle_price IS NULL"
_NEW_KIND_VALUE_CHECK = (
    "(kind = 'PERCENT_OFF' AND discount_pct BETWEEN 1 AND 99"
    f" AND fixed_price IS NULL AND amount_off IS NULL{_NO_OTHERS})"
    " OR (kind = 'FIXED_PRICE' AND fixed_price > 0"
    f" AND discount_pct IS NULL AND amount_off IS NULL{_NO_OTHERS})"
    " OR (kind = 'AMOUNT_OFF' AND amount_off > 0"
    f" AND discount_pct IS NULL AND fixed_price IS NULL{_NO_OTHERS})"
    " OR (kind = 'BUY_N_GET_M' AND buy_qty BETWEEN 1 AND 99 AND free_qty BETWEEN 1 AND 99"
    " AND discount_pct IS NULL AND fixed_price IS NULL AND amount_off IS NULL"
    " AND bundle_price IS NULL AND NOT applies_consignment)"
    " OR (kind = 'BUNDLE' AND bundle_price > 0"
    " AND discount_pct IS NULL AND fixed_price IS NULL AND amount_off IS NULL"
    f"{_NO_BNGM} AND NOT applies_consignment)"
)


def _kind_in(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


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
    op.add_column("campaigns", sa.Column("bundle_price", sa.Numeric(12, 0), nullable=True))
    op.drop_constraint("campaignkind", "campaigns", type_="check")
    op.create_check_constraint("campaignkind", "campaigns", _kind_in(_NEW_KINDS))
    op.drop_constraint("ck_campaigns_kind_value", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_kind_value", "campaigns", _NEW_KIND_VALUE_CHECK)

    op.create_table(
        "campaign_bundle_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("campaign_id", "slot_no", name="uq_campaign_bundle_slots_no"),
        sa.CheckConstraint("qty BETWEEN 1 AND 99", name="ck_campaign_bundle_slots_qty"),
    )
    op.create_index("ix_campaign_bundle_slots_store_id", "campaign_bundle_slots", ["store_id"])
    op.create_index(
        "ix_campaign_bundle_slots_campaign_id", "campaign_bundle_slots", ["campaign_id"]
    )
    op.create_table(
        "campaign_bundle_slot_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "slot_id", sa.Integer(), sa.ForeignKey("campaign_bundle_slots.id"), nullable=False
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
            "slot_id", "target_type", "target_id", name="uq_bundle_slot_targets_entry"
        ),
    )
    op.create_index(
        "ix_campaign_bundle_slot_targets_store_id", "campaign_bundle_slot_targets", ["store_id"]
    )
    op.create_index(
        "ix_campaign_bundle_slot_targets_slot_id", "campaign_bundle_slot_targets", ["slot_id"]
    )

    op.create_table(
        "sale_bundle_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("bundle_price", sa.Numeric(12, 0), nullable=False),
        sa.Column("returned_return_id", sa.Integer(), sa.ForeignKey("returns.id"), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("bundle_price > 0", name="ck_sale_bundle_groups_price_pos"),
    )
    for column in ("store_id", "sale_id", "campaign_id"):
        op.create_index(f"ix_sale_bundle_groups_{column}", "sale_bundle_groups", [column])
    op.create_table(
        "sale_bundle_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "bundle_group_id", sa.Integer(), sa.ForeignKey("sale_bundle_groups.id"), nullable=False
        ),
        sa.Column("sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id"), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("bundle_group_id", "sale_line_id", name="uq_sale_bundle_members_entry"),
        sa.CheckConstraint("qty > 0", name="ck_sale_bundle_members_qty_pos"),
    )
    for column in ("store_id", "bundle_group_id", "sale_line_id"):
        op.create_index(f"ix_sale_bundle_members_{column}", "sale_bundle_members", [column])


def downgrade() -> None:
    op.drop_table("sale_bundle_members")
    op.drop_table("sale_bundle_groups")
    op.drop_table("campaign_bundle_slot_targets")
    op.drop_table("campaign_bundle_slots")
    op.drop_constraint("ck_campaigns_kind_value", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_kind_value", "campaigns", _OLD_KIND_VALUE_CHECK)
    op.drop_constraint("campaignkind", "campaigns", type_="check")
    op.create_check_constraint("campaignkind", "campaigns", _kind_in(_OLD_KINDS))
    op.drop_column("campaigns", "bundle_price")
