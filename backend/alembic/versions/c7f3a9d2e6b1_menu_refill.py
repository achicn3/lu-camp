"""Menu refill (2026-10-16): refillable items and the refill price percentage setting."""

import sqlalchemy as sa
from alembic import op

revision = "c7f3a9d2e6b1"
down_revision = "b5e2c8f4a1d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "menu_items",
        sa.Column("refillable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "settings",
        sa.Column("menu_refill_pct", sa.Integer(), nullable=False, server_default=sa.text("80")),
    )
    op.create_check_constraint(
        "ck_settings_menu_refill_pct",
        "settings",
        "menu_refill_pct BETWEEN 1 AND 99",
    )


def downgrade() -> None:
    op.drop_constraint("ck_settings_menu_refill_pct", "settings", type_="check")
    op.drop_column("settings", "menu_refill_pct")
    op.drop_column("menu_items", "refillable")
