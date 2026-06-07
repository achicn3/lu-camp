"""add store phone

Revision ID: 7c4e9a2b1f30
Revises: 1ee6d8890a79
Create Date: 2026-06-07 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c4e9a2b1f30"
down_revision: str | Sequence[str] | None = "1ee6d8890a79"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("stores", sa.Column("phone", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("stores", "phone")
