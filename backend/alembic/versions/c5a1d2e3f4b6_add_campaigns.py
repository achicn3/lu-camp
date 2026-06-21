"""add campaigns (門市活動)

Revision ID: c5a1d2e3f4b6
Revises: b8e4f9a2c6d1
Create Date: 2026-06-21 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a1d2e3f4b6"
down_revision: str | Sequence[str] | None = "b8e4f9a2c6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("discount_pct", sa.Integer(), nullable=False),
        sa.Column(
            "applies_owned_serialized",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "applies_owned_bulk", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("applies_catalog", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "applies_consignment", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "consignment_discount_bearing",
            sa.Enum(
                "STORE_ABSORBS",
                "PROPORTIONAL",
                name="consignmentdiscountbearing",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            server_default="STORE_ABSORBS",
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "ACTIVE",
                "ENDED",
                "CANCELLED",
                name="campaignstatus",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "discount_pct >= 1 AND discount_pct <= 99", name="ck_campaigns_discount_pct"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_campaigns_window"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_campaigns_store_id"), "campaigns", ["store_id"], unique=False)
    op.create_index(
        "uq_one_active_campaign_per_store",
        "campaigns",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_one_active_campaign_per_store", table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_store_id"), table_name="campaigns")
    op.drop_table("campaigns")
