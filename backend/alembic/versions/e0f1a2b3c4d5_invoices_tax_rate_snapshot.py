"""invoices：結帳當下的稅率快照（F0401 金額欄位以快照計，不用活 settings）

docs/24（Codex 第九輪）：B2B 分稅若在上送時讀活 settings.tax_rate，結帳後改稅率會讓
送出的 F0401 與本地帳（invoice.net/tax）不一致。稅率隨發票落地凍結；既有列以預設 5% 回填
（本店唯一歷史稅率）。

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0.05"),
    )


def downgrade() -> None:
    op.drop_column("invoices", "tax_rate")
