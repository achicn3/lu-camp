"""add categories and category_pricing_rules

Revision ID: d2e3f4a5b6c7
Revises: c1a2b3d4e5f6
Create Date: 2026-06-16 10:30:00.000000

F6 A2/A3：分類（定價骨幹）與分類×成色帶定價規則（雙重約束參數）。規則於建分類時 seed（service 層）。
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1a2b3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list["sa.Column[Any]"]:
    now = sa.text("now()")
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("target_margin_pct", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("store_id", "name", name="uq_categories_store_name"),
    )
    op.create_index(op.f("ix_categories_store_id"), "categories", ["store_id"])

    op.create_table(
        "category_pricing_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column(
            "condition_band",
            sa.Enum(
                "S",
                "A",
                "B",
                "C",
                "D",
                "E",
                name="grade",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("discount_ceiling_pct", sa.Integer(), nullable=False),
        sa.Column("min_margin_pct", sa.Integer(), nullable=False),
        sa.Column("min_price_multiple", sa.Numeric(precision=5, scale=2), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "store_id", "category_id", "condition_band", name="uq_category_pricing_rule_band"
        ),
        sa.CheckConstraint("condition_band <> 'E'", name="ck_pricing_rule_band_not_e"),
    )
    op.create_index(
        op.f("ix_category_pricing_rules_store_id"), "category_pricing_rules", ["store_id"]
    )
    op.create_index(
        op.f("ix_category_pricing_rules_category_id"), "category_pricing_rules", ["category_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_category_pricing_rules_category_id"), "category_pricing_rules")
    op.drop_index(op.f("ix_category_pricing_rules_store_id"), "category_pricing_rules")
    op.drop_table("category_pricing_rules")
    op.drop_index(op.f("ix_categories_store_id"), "categories")
    op.drop_table("categories")
