"""catalog_products 新增 is_active（停售）

2026-09-17 裁示：一般商品賣過、進過貨之後就刪不掉（採購單與交易紀錄要留著），
但原本連「藏起來」都做不到——誤建的商品只能一直掛在庫存頁與 POS 上。

加一個停售旗標：只影響清單與 POS 找不找得到，庫存數量、採購單、交易紀錄一概不動。
既有商品一律視為在售（true）。

Revision ID: c5e2a9d47f10
Revises: b8d1f3a6c209
Create Date: 2026-09-17 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e2a9d47f10"
down_revision: str | Sequence[str] | None = "b8d1f3a6c209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "catalog_products",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    """Downgrade schema.

    降版後停售的商品會全部回到清單與 POS（旗標消失）；資料本身不受影響。
    """
    op.drop_column("catalog_products", "is_active")
