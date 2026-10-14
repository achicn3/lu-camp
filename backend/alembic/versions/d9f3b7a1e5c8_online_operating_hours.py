"""Weekly online ordering hours; disabled for existing stores."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d9f3b7a1e5c8"
down_revision = "a7e3c9f1b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "online_order_hours",
            JSONB(),
            nullable=False,
            server_default=sa.text(
                '\'{"enabled": false,"days":[null,null,null,null,null,null,null]}\'::jsonb'
            ),
        ),
    )
    op.add_column(
        "online_order_links",
        sa.Column("within_hours", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "online_order_links",
        sa.Column("payment_alerts", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "online_order_links",
        sa.Column("payment_alert_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("online_order_links", "payment_alert_count")
    op.drop_column("online_order_links", "payment_alerts")
    op.drop_column("online_order_links", "within_hours")
    op.drop_column("settings", "online_order_hours")
