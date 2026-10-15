"""Guest-declared add-on orders skip the table minimum (staff judge on the spot)."""

import sqlalchemy as sa
from alembic import op

revision = "f1a7c3e9b2d4"
down_revision = "e4b8c2d6f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "online_orders",
        sa.Column("add_on", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("online_orders", "add_on")
