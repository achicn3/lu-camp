"""invoices：作廢狀態與作廢原因必須一致

`void_reason` 原本只有「值必須在列舉內」的 CHECK，於是允許兩種說不通的資料：
- 狀態是 VOID／VOID_PENDING 卻沒有原因 → 事後無從分辨「打錯單作廢」與「客人全退」
- 狀態不是作廢卻帶著 FULL_RETURN → 憑空出現的作廢理由

`void_invoice_for_sale()` 的 reason 有預設值（SALE_VOID），所有寫入路徑都會帶原因；
此 CHECK 是把那個保證釘在資料庫層，避免日後有人繞過 service 寫入。

Revision ID: f7a8b9c0d1e2
Revises: a7b8c9d0e1f2
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "ck_invoices_void_reason_matches_status"
_CONDITION = (
    "(status IN ('VOID', 'VOID_PENDING')) = (void_reason IS NOT NULL)"
)


def upgrade() -> None:
    # 先補齊：理論上不存在，但舊資料若有遺漏就先歸為 SALE_VOID（此前唯一的作廢來源）。
    op.execute(
        sa.text(
            "UPDATE invoices SET void_reason = 'SALE_VOID' "
            "WHERE status IN ('VOID', 'VOID_PENDING') AND void_reason IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE invoices SET void_reason = NULL "
            "WHERE status NOT IN ('VOID', 'VOID_PENDING') AND void_reason IS NOT NULL"
        )
    )
    op.create_check_constraint(_CK, "invoices", sa.text(_CONDITION))


def downgrade() -> None:
    op.drop_constraint(_CK, "invoices", type_="check")
