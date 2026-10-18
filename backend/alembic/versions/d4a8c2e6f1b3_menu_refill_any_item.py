"""Menu refill any item (2026-10-19): a setting that lets every menu item be refilled."""

import sqlalchemy as sa
from alembic import op

revision = "d4a8c2e6f1b3"
down_revision = "c7f3a9d2e6b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "menu_refill_any_item", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("settings", "menu_refill_any_item")
