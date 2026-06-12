"""add cash movement note

Revision ID: 8b4c7e2f9a31
Revises: 3f2a9c1d5e84
Create Date: 2026-06-12 09:00:00.000000

手動現金調整的事由（留痕，CLAUDE.md §5；Codex review 2026-06-12）：
原 API 收 note 但靜默丟棄。系統產生的異動為 NULL。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b4c7e2f9a31"
down_revision: str | Sequence[str] | None = "3f2a9c1d5e84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("cash_movements", sa.Column("note", sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("cash_movements", "note")
