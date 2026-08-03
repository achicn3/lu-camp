"""cart_sessions 保存店員端原始請求（贈品原因與折扣意圖）

客顯快照只放客人看得到的東西：沒有贈品原因、備註，也沒有折扣意圖。POS 重整後若只靠快照
重建購物車，贈品與折扣會整個掉光；而 hydration 之後的同步 effect 又會把這份殘缺狀態寫回
伺服器——混合單會刪掉贈品與折扣，純贈品單甚至直接取消整張權威購物車。

既有購物車一律留 NULL（都是升級前建立的短命草稿），POS 會退回「只以快照重建」並停止回寫。

Revision ID: d8f0b2c4e6a1
Revises: c7e9a1b3d5f2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8f0b2c4e6a1"
down_revision: str | None = "c7e9a1b3d5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cart_sessions",
        sa.Column("staff_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cart_sessions", "staff_payload")
