"""K5 購物金扣抵手持簽署（docs/23 D3）：settings.require_store_credit_signing＋
sales.signature_task_id（FK＋單次使用 UNIQUE）

以購物金付款的結帳可綁定已簽 STORE_CREDIT_USE 任務；政策開啟後為必要。一份簽署至多綁一筆銷售。

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: str | Sequence[str] | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "settings",
        sa.Column(
            "require_store_credit_signing",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "sales",
        sa.Column("signature_task_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_sales_signature_task",
        "sales",
        "signature_tasks",
        ["signature_task_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_sales_signature_task", "sales", ["signature_task_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_sales_signature_task", "sales", type_="unique")
    op.drop_constraint("fk_sales_signature_task", "sales", type_="foreignkey")
    op.drop_column("sales", "signature_task_id")
    op.drop_column("settings", "require_store_credit_signing")
