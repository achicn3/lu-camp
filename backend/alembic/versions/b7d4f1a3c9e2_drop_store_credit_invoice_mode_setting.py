"""混合付款一律扣掉購物金後開發票：拿掉 settings.store_credit_invoice_mode（店主 2026-10-08 統一）。

ADR-029 原本可在設定切換「扣掉購物金後開」／「整筆開＋購物金折讓」；店主裁示統一為前者、不再切換。
`invoices.store_credit_mode` 保留——切換期間用折讓模式開出的舊發票，作廢／退貨仍要照它處理。

Revision ID: b7d4f1a3c9e2
Revises: e3a9c5d1f7b2
"""

import sqlalchemy as sa
from alembic import op

revision = "b7d4f1a3c9e2"
down_revision = "e3a9c5d1f7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 欄位上的 CHECK（storecreditinvoicemode）隨欄位一起刪；invoices 上同名的那條不受影響。
    op.drop_column("settings", "store_credit_invoice_mode")


def downgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "store_credit_invoice_mode",
            sa.Enum(
                "DEDUCT",
                "ALLOWANCE",
                name="storecreditinvoicemode",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
            server_default="DEDUCT",
        ),
    )
