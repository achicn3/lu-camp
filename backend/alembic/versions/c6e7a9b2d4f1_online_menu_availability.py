"""Durable current online menu availability.

Revision ID: c6e7a9b2d4f1
Revises: 4f1c8e2a9b70
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c6e7a9b2d4f1"
down_revision = "4f1c8e2a9b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "online_menu_availability",
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), primary_key=True),
        sa.Column("menu_version", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("delivery_state", sa.String(10), nullable=False),
        sa.Column("last_error", sa.String(200)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("revision >= 0", name="ck_online_menu_availability_revision"),
        sa.CheckConstraint(
            "delivery_state IN ('PENDING', 'DELIVERED', 'CONFLICT')",
            name="ck_online_menu_availability_delivery",
        ),
    )


def downgrade() -> None:
    op.drop_table("online_menu_availability")
