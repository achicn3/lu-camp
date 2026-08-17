"""手開紙本發票登記：invoices.issue_channel（docs/36）

既有紀錄全部視為 AMEGO（經加值中心開立）。MANUAL_PAPER 代表字軌用完/平台故障時
以紙本備用發票手開——平台上沒有這張，不可走 F0501／G0401，也不印證明聯。

Revision ID: c7a9e1b3d5f7
Revises: b4d6f8a1c3e5
Create Date: 2026-08-17 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a9e1b3d5f7"
down_revision: str | Sequence[str] | None = "b4d6f8a1c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "einvoiceissuechannel"
# 與 `app.shared.enums.EInvoiceIssueChannel` 同步
# （由 test_enum_check_constraint_sync 守衛：只加 enum 值卻忘了改 migration，
# 測試庫是 create_all 建的會全綠、真 DB 卻寫不進去）。
_CHANNELS = ("AMEGO", "MANUAL_PAPER")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "invoices",
        sa.Column(
            "issue_channel",
            sa.String(length=30),
            nullable=False,
            server_default="AMEGO",
        ),
    )
    allowed = ", ".join(f"'{v}'" for v in _CHANNELS)
    op.create_check_constraint(_CK, "invoices", sa.text(f"issue_channel IN ({allowed})"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CK, "invoices", type_="check")
    op.drop_column("invoices", "issue_channel")
