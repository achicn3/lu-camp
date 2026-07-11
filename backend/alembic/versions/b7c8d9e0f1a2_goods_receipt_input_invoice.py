"""goods_receipts 進項發票欄（裁示 2026-07-11）：收貨時登錄供應商發票

號碼（2 英文+8 數字）/日期/含稅金額＋未稅/稅額拆分（net+tax=total CHECK）；全空＝未登錄。

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("goods_receipts", sa.Column("invoice_number", sa.String(length=10)))
    op.add_column("goods_receipts", sa.Column("invoice_date", sa.Date()))
    op.add_column("goods_receipts", sa.Column("invoice_total", sa.Numeric(12, 0)))
    op.add_column("goods_receipts", sa.Column("invoice_net", sa.Numeric(12, 0)))
    op.add_column("goods_receipts", sa.Column("invoice_tax", sa.Numeric(12, 0)))
    op.create_check_constraint(
        "ck_goods_receipts_invoice_consistent",
        "goods_receipts",
        "(invoice_number IS NULL AND invoice_date IS NULL AND invoice_total IS NULL"
        " AND invoice_net IS NULL AND invoice_tax IS NULL)"
        " OR (invoice_number IS NOT NULL AND invoice_date IS NOT NULL"
        " AND invoice_total IS NOT NULL AND invoice_net IS NOT NULL"
        " AND invoice_tax IS NOT NULL AND invoice_net + invoice_tax = invoice_total)",
    )
    op.create_check_constraint(
        "ck_goods_receipts_invoice_number_format",
        "goods_receipts",
        "invoice_number IS NULL OR invoice_number ~ '^[A-Z]{2}[0-9]{8}$'",
    )
    op.create_index(
        "uq_goods_receipts_store_invoice",
        "goods_receipts",
        ["store_id", "invoice_number", "invoice_date"],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_goods_receipts_store_invoice", table_name="goods_receipts")
    op.drop_constraint("ck_goods_receipts_invoice_number_format", "goods_receipts")
    op.drop_constraint("ck_goods_receipts_invoice_consistent", "goods_receipts")
    op.drop_column("goods_receipts", "invoice_tax")
    op.drop_column("goods_receipts", "invoice_net")
    op.drop_column("goods_receipts", "invoice_total")
    op.drop_column("goods_receipts", "invoice_date")
    op.drop_column("goods_receipts", "invoice_number")
