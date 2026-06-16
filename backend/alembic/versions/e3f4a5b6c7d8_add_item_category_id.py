"""add nullable category_id to serialized_items and bulk_lots

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-16 11:00:00.000000

F6 A3.5：品項分類 additive 持久化。先 nullable（既有資料無此欄、SC-2 不帶亦成立）；
FK→categories ON DELETE RESTRICT（分類有品項引用不可刪）。日後 backfill 後再小 migration
將 serialized_items.category_id 收緊為 NOT NULL。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    for table in ("serialized_items", "bulk_lots"):
        op.add_column(table, sa.Column("category_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_category_id",
            table,
            "categories",
            ["category_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("serialized_items", "bulk_lots"):
        op.drop_constraint(f"fk_{table}_category_id", table, type_="foreignkey")
        op.drop_column(table, "category_id")
