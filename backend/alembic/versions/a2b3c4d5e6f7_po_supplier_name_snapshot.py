"""purchase_orders 供應商名快照（下單當下的對方名，供歷史顯示/搜尋，供應商改名不改寫歷史）

Revision ID: a2b3c4d5e6f7
Revises: 9a1b2c3d4e5f
Create Date: 2026-07-14

採購單原只存 supplier_id，顯示/搜尋 join 目前 Supplier.name：供應商改名會回溯改寫所有歷史單、
原名也搜不到（Codex 對抗審 high）。改在建單當下快照供應商名到 purchase_orders.supplier_name。
既有採購單先以目前供應商名回填後設 NOT NULL。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "9a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "purchase_orders", sa.Column("supplier_name", sa.String(length=150), nullable=True)
    )
    # 既有單回填目前供應商名（每張 PO 皆有對應 suppliers 列）。
    op.execute(
        "UPDATE purchase_orders po SET supplier_name = s.name "
        "FROM suppliers s WHERE s.id = po.supplier_id"
    )
    op.alter_column("purchase_orders", "supplier_name", nullable=False)


def downgrade() -> None:
    op.drop_column("purchase_orders", "supplier_name")
