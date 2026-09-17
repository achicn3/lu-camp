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

    **有停售商品時一律中止**（比照 b7e2c9a4f1d6 的先例）：這一欄一旦拿掉，所有停售品
    會瞬間回到庫存清單與 POS 變成可售，而「哪些被停售」再也復原不了。要降版請先把
    停售商品處理掉（恢復上架或確認可以重新開賣）。
    """
    conn = op.get_bind()
    inactive = conn.execute(
        sa.text("SELECT count(*) FROM catalog_products WHERE is_active = false")
    ).scalar_one()
    if inactive:
        raise RuntimeError(
            f"有 {inactive} 件停售商品，降版會讓它們全部重新開賣；請先處理後再降版"
        )
    op.drop_column("catalog_products", "is_active")
