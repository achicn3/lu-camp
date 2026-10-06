"""Online menu presentation settings referencing existing menu items.

Revision ID: d8f2a4c6e901
Revises: c6e7a9b2d4f1
"""

import sqlalchemy as sa
from alembic import op

revision = "d8f2a4c6e901"
down_revision = "c6e7a9b2d4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "online_menu_presentations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "menu_item_id",
            sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("flavor_description", sa.String(120)),
        sa.Column("audience_description", sa.String(120)),
        sa.Column("is_recommended", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_new", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("limited_on", sa.Date()),
        sa.Column("show_remaining", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("low_stock_threshold", sa.Integer(), server_default="5", nullable=False),
        sa.Column("hide_sold_out", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", "menu_item_id", name="uq_online_menu_presentations_item"),
        sa.CheckConstraint(
            "low_stock_threshold BETWEEN 0 AND 9999",
            name="ck_online_menu_presentations_threshold",
        ),
    )
    op.create_index(
        "ix_online_menu_presentations_store_id", "online_menu_presentations", ["store_id"]
    )


def downgrade() -> None:
    op.drop_table("online_menu_presentations")
