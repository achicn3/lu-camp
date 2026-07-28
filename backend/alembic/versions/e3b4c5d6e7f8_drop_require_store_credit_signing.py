"""移除殭屍設定 settings.require_store_credit_signing（審計 F-2）

K5（docs/23 D3）落地後，購物金扣抵的手持簽名確認改為**無條件強制**——sales service 一律要求
綁定已簽 STORE_CREDIT_USE 任務，不再讀此旗標。欄位留著卻無人讀，會讓管理者以為切換有效
（切了仍強制），屬誤導面，故移除。

回滾（downgrade）重建欄位並填 false：語意等同「政策未開」，與移除前的預設一致；此時強制
邏輯仍在 service 端，行為不變。

Revision ID: e3b4c5d6e7f8
Revises: d2a3b4c5d6e7
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "d2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("settings", "require_store_credit_signing")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "settings",
        sa.Column(
            "require_store_credit_signing",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
