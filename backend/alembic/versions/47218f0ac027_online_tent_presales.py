"""寄售帳篷預售（docs/102、ADR-035）：帳篷還在賣家家裡就先上官網。

online_tent_listings 的 serialized_item_id 改可為 NULL（NULL＝還沒到店的預售），預售列另存
品牌／名稱／售價／賣家聯絡方式；到店建寄售單時接上序號品，presale_* 留作紀錄。

降版：已有預售（含已轉寄售的）時拒絕（預售資料會整批消失、沒有序號品的列也無法還原）。

Revision ID: 47218f0ac027
Revises: f60f1333e094
"""

import sqlalchemy as sa
from alembic import op

revision = "47218f0ac027"
down_revision = "f60f1333e094"
branch_labels = None
depends_on = None

_TABLE = "online_tent_listings"
_CHECK = "ck_online_tent_listings_item_or_presale"


def upgrade() -> None:
    op.alter_column(_TABLE, "serialized_item_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(_TABLE, sa.Column("presale_code", sa.String(20), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("presale_brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=True),
    )
    op.add_column(_TABLE, sa.Column("presale_name", sa.String(100), nullable=True))
    op.add_column(_TABLE, sa.Column("presale_price", sa.Numeric(12, 0), nullable=True))
    op.add_column(_TABLE, sa.Column("presale_seller_name", sa.String(50), nullable=True))
    op.add_column(_TABLE, sa.Column("presale_seller_phone", sa.String(30), nullable=True))
    op.add_column(_TABLE, sa.Column("presale_note", sa.String(500), nullable=True))
    op.add_column(
        _TABLE, sa.Column("presale_cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint(
        "uq_online_tent_listings_presale_code", _TABLE, ["store_id", "presale_code"]
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "serialized_item_id IS NOT NULL OR (presale_code IS NOT NULL"
        " AND presale_name IS NOT NULL AND presale_price IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_online_tent_listings_presale_price",
        _TABLE,
        "presale_price IS NULL OR presale_price > 0",
    )


def abort_if_presales_exist(conn: sa.engine.Connection) -> None:
    """有預售（含已轉寄售的）就拒絕降版：預售資料會整批消失、沒有序號品的列也無法還原。"""
    has_presale = conn.execute(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE} WHERE presale_code IS NOT NULL)")
    )
    if has_presale.scalar():
        raise RuntimeError(
            "已有寄售帳篷預售資料，降版會整批刪除；"
            "請先確認不再需要後手動刪掉 presale_code 不是 NULL 的列"
        )


def downgrade() -> None:
    abort_if_presales_exist(op.get_bind())
    op.drop_constraint("ck_online_tent_listings_presale_price", _TABLE, type_="check")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_constraint("uq_online_tent_listings_presale_code", _TABLE, type_="unique")
    for column in (
        "converted_at",
        "presale_cancelled_at",
        "presale_note",
        "presale_seller_phone",
        "presale_seller_name",
        "presale_price",
        "presale_name",
        "presale_brand_id",
        "presale_code",
    ):
        op.drop_column(_TABLE, column)
    op.alter_column(_TABLE, "serialized_item_id", existing_type=sa.Integer(), nullable=False)
