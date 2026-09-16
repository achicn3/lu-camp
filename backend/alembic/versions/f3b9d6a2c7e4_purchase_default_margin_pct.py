"""settings 新增 purchase_default_margin_pct（採購預設毛利率，預設 30）

2026-09-16 裁示：採購建立一般商品時要能填成本與毛利率、自動算建議售價。毛利率**逐件設定**
（帳篷與營繩的毛利本來就不同），這個設定只決定「欄位一打開先帶幾 %」，店長可自行調整而
不必改程式（CLAUDE.md §6：預設值放 settings、不得寫死）。

與收購的 `default_margin_pct`（45）**刻意分開**：二手品議價空間大、目標毛利本來就比新品高，
共用一個值會逼其中一邊將就。

Revision ID: f3b9d6a2c7e4
Revises: e6a2c8f4b1d9
Create Date: 2026-09-16 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b9d6a2c7e4"
down_revision: str | Sequence[str] | None = "e6a2c8f4b1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "settings",
        sa.Column(
            "purchase_default_margin_pct",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    只是個預設值，沒有商品資訊會因此遺失（每件商品的售價早已各自存在
    `catalog_products.unit_price`），直接刪欄即可。
    """
    op.drop_column("settings", "purchase_default_margin_pct")
