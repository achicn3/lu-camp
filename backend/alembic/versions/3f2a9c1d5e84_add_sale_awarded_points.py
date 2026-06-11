"""add sale awarded_points

Revision ID: 3f2a9c1d5e84
Revises: 7c4e9a2b1f30
Create Date: 2026-06-11 12:00:00.000000

記錄該筆銷售結帳時「實際累積」的會員點數（docs/16 §0）：void 沖回以此為準，
不重算——歷史單（本欄位上線前）預設 0、沖回 0；日後點數規則調整也不會錯沖。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f2a9c1d5e84"
down_revision: str | Sequence[str] | None = "7c4e9a2b1f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "sales",
        sa.Column("awarded_points", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sales", "awarded_points")
