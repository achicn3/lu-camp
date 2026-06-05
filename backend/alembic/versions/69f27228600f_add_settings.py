"""add settings

Revision ID: 69f27228600f
Revises: 62e9ec3746b1
Create Date: 2026-06-05 17:57:07.760357

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "69f27228600f"
down_revision: str | Sequence[str] | None = "62e9ec3746b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column(
            "einvoice_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "tax_rate",
            sa.Numeric(precision=5, scale=4),
            server_default=sa.text("0.05"),
            nullable=False,
        ),
        sa.Column(
            "default_commission_pct", sa.Integer(), server_default=sa.text("50"), nullable=False
        ),
        sa.Column("default_margin_pct", sa.Integer(), server_default=sa.text("45"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", name="uq_settings_store_id"),
    )
    op.create_index(op.f("ix_settings_store_id"), "settings", ["store_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_settings_store_id"), table_name="settings")
    op.drop_table("settings")
