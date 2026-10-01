"""餐飲損耗：報廢時凍結成本、退款記下「還能賣」（docs/49 F2）。

Revision ID: edaa5e5ce783
Revises: 31f4b20c0acc
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "edaa5e5ce783"
down_revision = "31f4b20c0acc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "menu_stock_adjustments",
        sa.Column("unit_cost_snapshot", sa.Numeric(12, 0), nullable=True),
    )
    op.add_column(
        "return_lines",
        sa.Column("resellable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def abort_if_waste_data_exists(conn: Connection) -> None:
    """損耗成本與「還能賣」是報表的依據，降版會丟掉；有資料就中止。"""
    found = []
    n = conn.execute(
        sa.text("SELECT count(*) FROM menu_stock_adjustments WHERE unit_cost_snapshot IS NOT NULL")
    ).scalar_one()
    if n:
        found.append(f"報廢成本快照 {n} 筆")
    n = conn.execute(sa.text("SELECT count(*) FROM return_lines WHERE resellable")).scalar_one()
    if n:
        found.append(f"還能賣的退款明細 {n} 筆")
    if found:
        raise RuntimeError("拒絕降版：以下資料降版後無處保存——" + "、".join(found))


def downgrade() -> None:
    abort_if_waste_data_exists(op.get_bind())
    op.drop_column("return_lines", "resellable")
    op.drop_column("menu_stock_adjustments", "unit_cost_snapshot")
