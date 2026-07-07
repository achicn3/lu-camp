"""add signature_tasks.sign_idempotency_key（手持簽署冪等重送，docs/23 Codex K3 第六輪）

回應遺失時客端以同鍵重送 → 若已簽成回放同結果而非 409，避免曖昧失敗使裝置卡住/洩漏下一位任務。

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "signature_tasks",
        sa.Column("sign_idempotency_key", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("signature_tasks", "sign_idempotency_key")
