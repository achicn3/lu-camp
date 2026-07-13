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
    # 舊資料回填：既有 CLOSED 語意為「已結案（已收貨）」→ 併入 RECEIVED；已收貨（含剛併入的
    # CLOSED）採購單的明細其待收為 0，故 received_qty 補為 qty，避免升級後顯示「已收貨但已收 0」
    # 且卡在完成狀態無法再收（Codex 第一輪 blocker）。
    op.execute("UPDATE purchase_orders SET status = 'RECEIVED' WHERE status = 'CLOSED'")
    op.execute(
        "UPDATE purchase_order_lines l SET received_qty = l.qty "
        "FROM purchase_orders p "
        "WHERE l.purchase_order_id = p.id AND p.status = 'RECEIVED'"
    )
    op.create_check_constraint(
        "ck_purchase_order_lines_received_qty_range",
        "purchase_order_lines",
        "received_qty >= 0 AND received_qty <= qty",
    )
    # 2) 狀態值集：移除 CLOSED、加入 PARTIAL/CANCELLED（CLOSED 已於上一步併入 RECEIVED）
    op.drop_constraint("purchase_orders_status_check", "purchase_orders", type_="check")
    op.create_check_constraint(
        "purchase_orders_status_check", "purchase_orders", _status_check(_STATUS_NEW)
    )
    # 3) 解除「一 PO 一收貨」限制，支援分批收貨
    op.drop_constraint(
        "uq_goods_receipts_purchase_order_id", "goods_receipts", type_="unique"
    )
    # 4) 分批收貨冪等（防網路重試重複入庫）：Idempotency-Key＋請求指紋，(store, key) 部分唯一。
    op.add_column("goods_receipts", sa.Column("idempotency_key", sa.String(length=80)))
    op.add_column("goods_receipts", sa.Column("request_fingerprint", sa.String(length=64)))
    op.create_index(
        "uq_goods_receipts_store_idempotency",
        "goods_receipts",
        ["store_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """此版不可無損降級；在任何 schema 變更前明確拒絕。

    舊版一張 PO 只能有一張 receipt，且沒有 PARTIAL/CANCELLED/received_qty，無法保留正常使用
    後產生的多批收貨與部分到貨語意。事故復原請保留目前 DB、修正程式後 roll forward；若必須回復
    應用程式，應部署相容目前 schema 的 hotfix，不可執行 alembic downgrade。
    """
    raise RuntimeError(
        "irreversible purchasing migration; preserve the database and roll forward with a "
        "schema-compatible hotfix"
    )
