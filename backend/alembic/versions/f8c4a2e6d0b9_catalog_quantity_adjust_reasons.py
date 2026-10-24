"""一般商品手動調整數量的異動原因（店主 2026-10-24）。

- stock_movements.reason 加 DAMAGED／EXPIRED／SELF_USE／COUNT_CORRECTION／OTHER_ADJUST：
  編輯視窗直接改數量時選的原因，直接當異動原因寫帳。

Revision ID: f8c4a2e6d0b9
Revises: e6b2d8f4a3c1
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "f8c4a2e6d0b9"
down_revision = "e6b2d8f4a3c1"
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
    "PURCHASE_CORRECTION",
)
_ADDED_REASONS = ("DAMAGED", "EXPIRED", "SELF_USE", "COUNT_CORRECTION", "OTHER_ADJUST")
_NEW_REASONS = (*_OLD_REASONS, *_ADDED_REASONS)


def _reasons(values: tuple[str, ...]) -> None:
    op.drop_constraint(_STOCK_REASON_CK, "stock_movements", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        _STOCK_REASON_CK, "stock_movements", sa.text(f"reason IN ({allowed})")
    )


def upgrade() -> None:
    _reasons(_NEW_REASONS)


def abort_if_adjustments_exist(conn: Connection) -> None:
    """舊程式認不得手動調整異動；已有就中止降版。"""
    count = conn.execute(
        sa.text("SELECT count(*) FROM stock_movements WHERE reason = ANY(:reasons)"),
        {"reasons": list(_ADDED_REASONS)},
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"拒絕降版：已有 {count} 筆手動調整數量異動；請留在新版程式、不要降資料庫"
        )


def downgrade() -> None:
    abort_if_adjustments_exist(op.get_bind())
    _reasons(_OLD_REASONS)
