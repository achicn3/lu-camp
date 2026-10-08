"""線上 LINE Pay 已付款的單（docs/44 §4.4.2；O5b）。

- online_orders 加 linepay_order_id／linepay_transaction_id（雲端回報已付款才有）、發票載具／統編、
  attention（POS 沒辦法自動成立銷售的原因，例如價格與客人付的不同）。
- linepay_transactions 加 channel（OFFLINE 門市掃碼／ONLINE 客人手機付）：線上付款的退款要走交易號。

Revision ID: d5f1b3c7e9a2
Revises: c3e7a9d1f5b8
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "d5f1b3c7e9a2"
down_revision = "c3e7a9d1f5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("online_orders", sa.Column("linepay_order_id", sa.String(64), nullable=True))
    op.add_column(
        "online_orders", sa.Column("linepay_transaction_id", sa.String(32), nullable=True)
    )
    op.add_column("online_orders", sa.Column("invoice_carrier", sa.String(8), nullable=True))
    op.add_column("online_orders", sa.Column("invoice_tax_id", sa.String(8), nullable=True))
    op.add_column("online_orders", sa.Column("attention", sa.String(300), nullable=True))
    op.add_column(
        "linepay_transactions",
        sa.Column("channel", sa.String(10), nullable=False, server_default=sa.text("'OFFLINE'")),
    )
    op.create_check_constraint(
        "ck_linepay_transactions_channel",
        "linepay_transactions",
        "channel IN ('OFFLINE', 'ONLINE')",
    )


def abort_if_online_linepay_exists(conn: Connection) -> None:
    """降版會失去「這筆是線上付款、要用交易號退款」的資訊；已有就中止。"""
    online = conn.execute(
        sa.text("SELECT count(*) FROM linepay_transactions WHERE channel = 'ONLINE'")
    ).scalar_one()
    paid = conn.execute(
        sa.text("SELECT count(*) FROM online_orders WHERE linepay_transaction_id IS NOT NULL")
    ).scalar_one()
    if online or paid:
        raise RuntimeError(
            f"拒絕降版：已有 {online} 筆線上 LINE Pay 收款、{paid} 張已付款線上單；"
            "只退程式、不要降資料庫"
        )


def downgrade() -> None:
    abort_if_online_linepay_exists(op.get_bind())
    op.drop_constraint("ck_linepay_transactions_channel", "linepay_transactions", type_="check")
    op.drop_column("linepay_transactions", "channel")
    for column in (
        "attention",
        "invoice_tax_id",
        "invoice_carrier",
        "linepay_transaction_id",
        "linepay_order_id",
    ):
        op.drop_column("online_orders", column)
