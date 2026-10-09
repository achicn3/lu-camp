"""採購單事後修改的庫存更正原因（docs/70 §4.4；ADR-030；店主 2026-10-08）。

- stock_movements.reason 加 PURCHASE_CORRECTION：已收數量／商品改了，差額加減庫存。

Revision ID: e2a6c8f4b1d7
Revises: d5f1b3c7e9a2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "e2a6c8f4b1d7"
down_revision = "d5f1b3c7e9a2"
branch_labels = None
depends_on = None

_STOCK_REASON_CK = "stockreason"
_OLD_REASONS = (
    "ACQUISITION",
    "PURCHASE",
    "SALE",
    "RETURN",
    "CONSIGN_RETURN",
    "GIFT",
    "GIFT_RETURN",
    "WRITE_OFF",
    "STOCKTAKE",
    "ONLINE_HOLD",
    "ONLINE_RELEASE",
)
_NEW_REASONS = (*_OLD_REASONS, "PURCHASE_CORRECTION")


def _reasons(values: tuple[str, ...]) -> None:
    op.drop_constraint(_STOCK_REASON_CK, "stock_movements", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        _STOCK_REASON_CK, "stock_movements", sa.text(f"reason IN ({allowed})")
    )


def upgrade() -> None:
    _reasons(_NEW_REASONS)


def abort_if_corrections_exist(conn: Connection) -> None:
    """舊程式認不得更正異動；已有就中止降版。"""
    count = conn.execute(
        sa.text("SELECT count(*) FROM stock_movements WHERE reason = 'PURCHASE_CORRECTION'")
    ).scalar_one()
    if count:
        raise RuntimeError(f"拒絕降版：已有 {count} 筆採購更正異動；請留在新版程式、不要降資料庫")


def downgrade() -> None:
    abort_if_corrections_exist(op.get_bind())
    _reasons(_OLD_REASONS)
