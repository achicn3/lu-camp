"""SIGNED ACQUISITION_AFFIDAVIT 須有 chosen_payout（docs/23 K4 Codex 第三輪）

已簽的收購切結必有客人撥款選擇（CASH/STORE_CREDIT）；DB CHECK 杜絕 legacy/匯入的
NULL 撥款壞列成為可綁定的證據。既有列由 sign_task 強制二選一，皆合規。

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "ck_signature_tasks_signed_affidavit_payout"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        _CK,
        "signature_tasks",
        "NOT (status = 'SIGNED' AND kind = 'ACQUISITION_AFFIDAVIT') "
        "OR chosen_payout IS NOT NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CK, "signature_tasks", type_="check")
