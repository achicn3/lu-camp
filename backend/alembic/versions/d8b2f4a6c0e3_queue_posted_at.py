"""einvoice_upload_queue.posted_at：真的開始送出的時點（docs/36）

**「凍結 payload」不等於「可能已送到平台」。** Amego 路徑在真正 POST 之前就會先寫入
xml_path/dropped_at 並 commit（認領持久化），接著才做對帳查詢、最後才送出。若對帳查詢
因斷網失敗，F0401 根本沒送出，但列上已有認領痕跡——手開登記若以那些痕跡判定「曾送出、
須向平台求證」，就會在**平台/網路故障時**（正是本功能存在的理由）鎖死、登記不了。

posted_at 只在真正呼叫送出端點之前寫入，是「可能已到平台」的精確證據。

Revision ID: d8b2f4a6c0e3
Revises: c7a9e1b3d5f7
Create Date: 2026-08-17 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b2f4a6c0e3"
down_revision: str | Sequence[str] | None = "c7a9e1b3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "einvoice_upload_queue",
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("einvoice_upload_queue", "posted_at")
