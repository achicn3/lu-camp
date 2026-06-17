"""add allow_clerk_manage_categories setting

Revision ID: f6a7b8c9d0e1
Revises: e3f4a5b6c7d8
Create Date: 2026-06-17 12:00:00.000000

F6 review：分類建立遵守 docs/13 的 MANAGER 預設與 clerk 開關。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "settings",
        sa.Column(
            "allow_clerk_manage_categories",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("settings", "allow_clerk_manage_categories")
