"""餐飲每日限量：限量開關、今日份數與版本、份數調整紀錄、明細扣量版本（docs/44 §3.7）。

「每天歸零」不靠排程：stock_qty 只在 stock_day＝今天時有效。新欄預設不限量，既有菜單行為不變。

Revision ID: b86c4571decf
Revises: 0a577011b3c1
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision = "b86c4571decf"
down_revision = "0a577011b3c1"
branch_labels = None
depends_on = None

_TABLES = ("menu_items", "menu_options")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "daily_limited", sa.Boolean(), server_default=sa.text("false"), nullable=False
            ),
        )
        op.add_column(table, sa.Column("stock_qty", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("stock_day", sa.Date(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "stock_generation", sa.Integer(), server_default=sa.text("0"), nullable=False
            ),
        )
        op.create_check_constraint(
            f"ck_{table}_stock_nonneg", table, "stock_qty IS NULL OR stock_qty >= 0"
        )
    # 結帳時扣到哪些每日限量對象、扣的是哪一版份數；作廢據此判斷能不能加回。
    op.add_column("sale_lines", sa.Column("menu_stock_consumed", postgresql.JSONB(), nullable=True))
    op.create_table(
        "menu_stock_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("target_kind", sa.String(10), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(reason = 'RESTOCK' AND delta > 0) "
            "OR (reason IN ('WASTE','CORRECTION') AND delta < 0)",
            name="ck_menu_stock_adjustments_reason_sign",
        ),
        sa.CheckConstraint(
            "target_kind IN ('item','option')", name="ck_menu_stock_adjustments_kind"
        ),
    )
    op.create_index("ix_menu_stock_adjustments_store_id", "menu_stock_adjustments", ["store_id"])
    op.create_index(
        "ix_menu_stock_adjustments_business_date", "menu_stock_adjustments", ["business_date"]
    )


def abort_if_daily_limits_exist(conn: Connection) -> None:
    """每日限量設定與份數調整紀錄（報廢統計）降版會刪掉；有資料就中止（當天份數則隔天本來就歸零）。"""
    found = []
    n = conn.execute(sa.text("SELECT count(*) FROM menu_stock_adjustments")).scalar_one()
    if n:
        found.append(f"menu_stock_adjustments 份數調整紀錄 {n} 筆")
    for table in _TABLES:
        n = conn.execute(sa.text(f"SELECT count(*) FROM {table} WHERE daily_limited")).scalar_one()
        if n:
            found.append(f"{table} 每日限量 {n} 筆")
    if found:
        raise RuntimeError("拒絕降版：以下設定降版後無處保存——" + "、".join(found))


def downgrade() -> None:
    abort_if_daily_limits_exist(op.get_bind())
    op.drop_table("menu_stock_adjustments")
    op.drop_column("sale_lines", "menu_stock_consumed")
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_stock_nonneg", table, type_="check")
        op.drop_column(table, "stock_generation")
        op.drop_column(table, "stock_day")
        op.drop_column(table, "stock_qty")
        op.drop_column(table, "daily_limited")
