"""Customer notes are removed from online ordering entirely (2026-10-15)."""

import sqlalchemy as sa
from alembic import op

revision = "a3d9e1f7c5b2"
down_revision = "f1a7c3e9b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("online_orders", "note")


def downgrade() -> None:
    op.add_column("online_orders", sa.Column("note", sa.String(200), nullable=True))
