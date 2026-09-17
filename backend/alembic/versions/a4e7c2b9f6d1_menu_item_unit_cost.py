"""menu_items 新增 unit_cost（餐飲成本）

2026-09-17 裁示：餐飲要能算毛利。菜單品項原本只有售價，賣出時成本記成 NULL（未知），
報表只看得到營收。加一欄成本，由店主自行加總（豆子、耗材、包材、蛋糕的材料…）後填入。

**刻意不做原料主檔與配方用量**：要讓系統自動算單品成本，得建原料、單位換算與扣庫存，
對單店不划算（同一裁示）。

可為 NULL＝不知道成本。**不預設 0**：0 會讓報表以為毛利 100%，比誠實留空更糟。
成交當下由 `sales.service` 快照到 `sale_lines.cost_snapshot`，日後調整成本不改寫歷史毛利
（沿用一般商品的既有口徑）。

Revision ID: a4e7c2b9f6d1
Revises: f3b9d6a2c7e4
Create Date: 2026-09-17 00:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4e7c2b9f6d1"
down_revision: str | Sequence[str] | None = "f3b9d6a2c7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("menu_items", sa.Column("unit_cost", sa.Numeric(12, 0), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    只刪這一欄。已成交的餐飲毛利存在 `sale_lines.cost_snapshot`，不受影響；
    降版後新賣出的餐飲會回到「成本未知」。
    """
    op.drop_column("menu_items", "unit_cost")
