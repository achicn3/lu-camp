"""Per-table minimum rules and guest party size for online orders."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e4b8c2d6f0a1"
down_revision = "d9f3b7a1e5c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "dine_in_minimums", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column("online_orders", sa.Column("party_size", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_online_orders_party_size", "online_orders", "party_size BETWEEN 1 AND 50"
    )


def downgrade() -> None:
    op.drop_constraint("ck_online_orders_party_size", "online_orders", type_="check")
    op.drop_column("online_orders", "party_size")
    op.drop_column("settings", "dine_in_minimums")
