"""invoices：結帳當下的稅率快照（F0401 金額欄位以快照計，不用活 settings）

docs/24（Codex 第九/十一輪）：B2B 分稅若在上送時讀活 settings.tax_rate，結帳後改稅率會讓
送出的 F0401 與本地帳（invoice.net/tax）不一致。稅率隨發票落地凍結；**既有列自其
net/tax 推導回填**（法定稅率皆整數百分比 → tax/net 取 2 位小數；tax=0 → 0），不得一刀切
5% 蓋掉非 5% 的歷史發票。回填後 fail-fast 驗證：快照必須能重現原拆分
（ROUND(total/(1+rate)) = net），否則整個 migration 失敗、需人工修正。

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

# 既有列回填（與測試共用同一 SQL 口徑）：法定稅率為整數百分比 → tax/net 四捨五入取 2 位。
BACKFILL_SQL = """
UPDATE invoices
SET tax_rate = CASE WHEN net > 0 THEN ROUND(tax / net, 2) ELSE 0 END
"""

# fail-fast：回填的快照必須重現原 net/tax 拆分（§6：net = ROUND_HALF_UP(total/(1+rate)））。
VERIFY_SQL = """
DO $$
DECLARE bad integer;
BEGIN
    SELECT count(*) INTO bad FROM invoices
    WHERE ROUND(total / (1 + tax_rate)) <> net;
    IF bad > 0 THEN
        RAISE EXCEPTION
            'invoices.tax_rate 回填無法重現 % 列的 net/tax 拆分，需人工修正後重跑', bad;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0.05"),
    )
    op.execute(BACKFILL_SQL)
    op.execute(VERIFY_SQL)


def downgrade() -> None:
    op.drop_column("invoices", "tax_rate")
