"""商品全新售價（原價）：serialized_items / bulk_lots 各加 retail_price（2026-09-19 裁示）

二手店開價要有對照數字：客人問「這頂帳篷值不值」，店員能指著標價說「全新要 8,000」。
**純記錄欄位**——不參與定價、毛利與報表的任何計算，查不到就留空（NULL）。

既有商品一律 NULL，不回填、不推估：憑空補一個「大概的全新價」會變成看起來像事實的猜測。

Revision ID: c3f8a1d40be7
Revises: e1a4c7d2b830
Create Date: 2026-09-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a1d40be7"
down_revision: str | Sequence[str] | None = "e1a4c7d2b830"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("serialized_items", "bulk_lots")


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        op.add_column(table, sa.Column("retail_price", sa.Numeric(12, 0), nullable=True))
        # 負的原價沒有意義；擋在 DB 讓任何寫入路徑（含日後的匯入腳本）都繞不過去。
        op.create_check_constraint(
            f"ck_{table}_retail_price_nonneg",
            table,
            "retail_price IS NULL OR retail_price >= 0",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_retail_price_nonneg", table, type_="check")
        op.drop_column(table, "retail_price")
