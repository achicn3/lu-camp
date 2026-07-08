"""add acquisitions.signature_task_id（手持切結綁定收購，docs/23 K4/D2）

一份已簽切結書至多綁一張收購單（UNIQUE 單次使用）；撥款須與切結任務 chosen_payout 一致。

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "acquisitions",
        sa.Column("signature_task_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_acquisitions_signature_task",
        "acquisitions",
        "signature_tasks",
        ["signature_task_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_acquisitions_signature_task", "acquisitions", ["signature_task_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_acquisitions_signature_task", "acquisitions", type_="unique")
    op.drop_constraint("fk_acquisitions_signature_task", "acquisitions", type_="foreignkey")
    op.drop_column("acquisitions", "signature_task_id")
