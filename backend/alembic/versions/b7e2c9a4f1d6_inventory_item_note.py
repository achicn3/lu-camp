"""add note column to the three inventory entities

Revision ID: b7e2c9a4f1d6
Revises: c4e8a2b6d9f1
Create Date: 2026-09-02 10:00:00.000000

商品備註（2026-09-02 裁示）：單一自由欄位，兼「商品狀況說明」與「內部作業備忘」。
三種庫存型態（序號單品／一般商品／散裝批）一律都加，避免「查得到 A 查不到 B」。
Additive、nullable、無 backfill：既有列一律 NULL＝沒有備註。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c9a4f1d6"
down_revision: str | Sequence[str] | None = "c4e8a2b6d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("serialized_items", "catalog_products", "bulk_lots")


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        op.add_column(table, sa.Column("note", sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for table in reversed(_TABLES):
        op.drop_column(table, "note")
