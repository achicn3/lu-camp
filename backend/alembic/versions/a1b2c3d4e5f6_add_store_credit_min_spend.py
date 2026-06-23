"""add settings.store_credit_min_spend (購物金低消門檻)

Revision ID: a1b2c3d4e5f6
Revises: f7b8c9d0e1a2
Create Date: 2026-06-23

購物金低消門檻（整數元）：非餐飲消費未達此值則不可折抵購物金。預設 0＝不限制。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f7b8c9d0e1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "store_credit_min_spend",
            sa.Numeric(12, 0),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("settings", "store_credit_min_spend")
