"""Online LINE Pay switch (2026-10-20): what the cloud says about LINE Pay, cached for the POS."""

import sqlalchemy as sa
from alembic import op

revision = "e6b2d8f4a3c1"
down_revision = "d4a8c2e6f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("online_order_links", sa.Column("linepay_enabled", sa.Boolean(), nullable=True))
    op.add_column(
        "online_order_links", sa.Column("linepay_configured", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("online_order_links", "linepay_configured")
    op.drop_column("online_order_links", "linepay_enabled")
