"""po status v2: partial receiving (received_qty, multi-receipt, new statuses)

Revision ID: c1d2e3f4a5b6
Revises: e0f1a2b3c4d5
Create Date: 2026-07-12

採購單狀態機改為 DRAFT/ORDERED/PARTIAL/RECEIVED/CANCELLED（移除 CLOSED）；採購明細加
received_qty（累計已收，0<=received_qty<=qty）；解除 goods_receipts 一 PO 一收貨的限制
以支援分批收貨。purchase_orders.status 為 native_enum=False（VARCHAR + CHECK
`purchase_orders_status_check`），改值集須 drop/recreate CHECK。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_OLD = ("DRAFT", "ORDERED", "RECEIVED", "CLOSED")
_STATUS_NEW = ("DRAFT", "ORDERED", "PARTIAL", "RECEIVED", "CANCELLED")


def _status_check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"(status)::text = ANY (ARRAY[{joined}]::text[])"


def upgrade() -> None:
    # 1) 明細加已收數量（分批收貨累加）＋範圍 CHECK
    op.add_column(
        "purchase_order_lines",
        sa.Column("received_qty", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_purchase_order_lines_received_qty_range",
        "purchase_order_lines",
        "received_qty >= 0 AND received_qty <= qty",
    )
    # 2) 狀態值集：移除 CLOSED、加入 PARTIAL/CANCELLED
    op.drop_constraint("purchase_orders_status_check", "purchase_orders", type_="check")
    op.create_check_constraint(
        "purchase_orders_status_check", "purchase_orders", _status_check(_STATUS_NEW)
    )
    # 3) 解除「一 PO 一收貨」限制，支援分批收貨
    op.drop_constraint(
        "uq_goods_receipts_purchase_order_id", "goods_receipts", type_="unique"
    )


def downgrade() -> None:
    # 還原前須確保無 CLOSED 以外的新狀態列、且無一 PO 多收貨列，否則約束建立會失敗。
    op.create_unique_constraint(
        "uq_goods_receipts_purchase_order_id", "goods_receipts", ["purchase_order_id"]
    )
    op.drop_constraint("purchase_orders_status_check", "purchase_orders", type_="check")
    op.create_check_constraint(
        "purchase_orders_status_check", "purchase_orders", _status_check(_STATUS_OLD)
    )
    op.drop_constraint(
        "ck_purchase_order_lines_received_qty_range",
        "purchase_order_lines",
        type_="check",
    )
    op.drop_column("purchase_order_lines", "received_qty")
