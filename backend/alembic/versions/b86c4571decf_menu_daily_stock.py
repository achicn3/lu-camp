"""餐飲每日限量：品項與選項的 daily_limited、stock_qty、stock_day（docs/44 §3.7）。

「每天歸零」不靠排程：stock_qty 只在 stock_day＝今天時有效。新欄預設不限量，既有菜單行為不變。

Revision ID: b86c4571decf
Revises: 0a577011b3c1
"""

import sqlalchemy as sa
from alembic import op
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
        op.create_check_constraint(
            f"ck_{table}_stock_nonneg", table, "stock_qty IS NULL OR stock_qty >= 0"
        )


def abort_if_daily_limits_exist(conn: Connection) -> None:
    """哪些品項／選項是每日限量是店主的設定，降版會刪掉；有設定就中止（當天份數則隔天本來就歸零）。"""
    found = []
    for table in _TABLES:
        n = conn.execute(sa.text(f"SELECT count(*) FROM {table} WHERE daily_limited")).scalar_one()
        if n:
            found.append(f"{table} 每日限量 {n} 筆")
    if found:
        raise RuntimeError("拒絕降版：以下設定降版後無處保存——" + "、".join(found))


def downgrade() -> None:
    abort_if_daily_limits_exist(op.get_bind())
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_stock_nonneg", table, type_="check")
        op.drop_column(table, "stock_day")
        op.drop_column(table, "stock_qty")
        op.drop_column(table, "daily_limited")
