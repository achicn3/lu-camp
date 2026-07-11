"""invoices：Amego 開立回傳的證明聯列印內容（一維條碼/左右 QR 內容字串）

docs/24：f0401 成功回應帶 barcode / qrcode_left / qrcode_right **內容字串**——證明聯
條碼/QR 以平台回傳為準（不再本地以 AES 產生）。經 invoice_query 對帳復原的發票
拿不到這三欄（平台查詢不回傳），允許 NULL。

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("barcode_text", sa.String(length=40), nullable=True))
    op.add_column("invoices", sa.Column("qrcode_left", sa.String(length=500), nullable=True))
    op.add_column("invoices", sa.Column("qrcode_right", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "qrcode_right")
    op.drop_column("invoices", "qrcode_left")
    op.drop_column("invoices", "barcode_text")
