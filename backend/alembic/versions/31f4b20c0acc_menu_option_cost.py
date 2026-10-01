"""選項成本：menu_options.unit_cost（docs/49 F1）。可空＝沒有額外材料，結帳時算 0。

Revision ID: 31f4b20c0acc
Revises: 3c59ce725956
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "31f4b20c0acc"
down_revision = "3c59ce725956"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("menu_options", sa.Column("unit_cost", sa.Numeric(12, 0), nullable=True))


def abort_if_option_costs_exist(conn: Connection) -> None:
    """選項成本是店主手填的，降版會丟掉；有填過就中止。"""
    n = conn.execute(
        sa.text("SELECT count(*) FROM menu_options WHERE unit_cost IS NOT NULL")
    ).scalar_one()
    if n:
        raise RuntimeError(f"拒絕降版：已有 {n} 個選項填了成本，降版後無處保存")


def downgrade() -> None:
    abort_if_option_costs_exist(op.get_bind())
    op.drop_column("menu_options", "unit_cost")
