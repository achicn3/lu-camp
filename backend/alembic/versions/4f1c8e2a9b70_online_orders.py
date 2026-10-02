"""線上訂單店內端（docs/44 §3.7、§4.3、§4.5、§4.6；O4b）。

- online_orders：從雲端拉到的單（remote_id 唯一＝重拉不重複匯入；sale_id 唯一＝只成立一筆銷售）。
- stock_reservations：拉單時直接扣的每日限量份數（帶入結帳加回再扣、取消／到期加回）。
- online_order_outbox：回報雲端的持久化重試佇列。
- online_order_links：和雲端的連線狀態（最後拉單、接單／暫停）。

Revision ID: 4f1c8e2a9b70
Revises: 7b2e4f19c0a5
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "4f1c8e2a9b70"
down_revision = "7b2e4f19c0a5"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "online_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("remote_id", sa.String(32), nullable=False),
        sa.Column("table_label", sa.String(20)),
        sa.Column("service_mode", sa.String(10), nullable=False),
        sa.Column("menu_version", sa.BigInteger(), nullable=False),
        sa.Column("total", sa.Numeric(12, 0), nullable=False),
        sa.Column("payment_method", sa.String(10), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column("lines", JSONB(), nullable=False),
        sa.Column("remote_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_status", sa.String(10), nullable=False),
        sa.Column("hold_status", sa.String(10), nullable=False),
        sa.Column("payment_status", sa.String(10), nullable=False),
        sa.Column("reject_reason", sa.String(300)),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id")),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by", sa.Integer(), sa.ForeignKey("users.id")),
        *_timestamps(),
        sa.UniqueConstraint("store_id", "remote_id", name="uq_online_orders_remote"),
        sa.UniqueConstraint("sale_id", name="uq_online_orders_sale"),
        sa.CheckConstraint("service_mode IN ('DINE_IN', 'TAKEOUT')", name="ck_online_orders_mode"),
        sa.CheckConstraint(
            "sync_status IN ('IMPORTED', 'SETTLED', 'VOIDED')", name="ck_online_orders_sync"
        ),
        sa.CheckConstraint(
            "hold_status IN ('NONE', 'HELD', 'REJECTED')", name="ck_online_orders_hold"
        ),
        sa.CheckConstraint(
            "payment_status IN ('UNPAID', 'PAID', 'CANCELLED')", name="ck_online_orders_payment"
        ),
        sa.CheckConstraint(
            "(sync_status = 'SETTLED') = (sale_id IS NOT NULL AND payment_status = 'PAID')",
            name="ck_online_orders_settled_sale",
        ),
        sa.CheckConstraint("total >= 0", name="ck_online_orders_total_nonneg"),
    )
    op.create_index("ix_online_orders_store_id", "online_orders", ["store_id"])

    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "online_order_id", sa.Integer(), sa.ForeignKey("online_orders.id"), nullable=False
        ),
        sa.Column("consumed", JSONB(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("online_order_id", name="uq_stock_reservations_order"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'CONVERTED', 'RELEASED', 'EXPIRED')",
            name="ck_stock_reservations_status",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE') = (ended_at IS NULL)", name="ck_stock_reservations_ended"
        ),
    )
    op.create_index("ix_stock_reservations_store_id", "stock_reservations", ["store_id"])
    op.create_index(
        "ix_stock_reservations_active",
        "stock_reservations",
        ["store_id", "expires_at"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "online_order_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "online_order_id", sa.Integer(), sa.ForeignKey("online_orders.id"), nullable=False
        ),
        sa.Column("remote_id", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(200)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'DEAD')", name="ck_online_order_outbox_status"
        ),
    )
    op.create_index("ix_online_order_outbox_store_id", "online_order_outbox", ["store_id"])
    op.create_index(
        "ix_online_order_outbox_online_order_id", "online_order_outbox", ["online_order_id"]
    )
    op.create_index(
        "ix_online_order_outbox_pending",
        "online_order_outbox",
        ["store_id", "id"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "online_order_links",
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), primary_key=True),
        sa.Column("accepting", sa.Boolean()),
        sa.Column("paused_reason", sa.String(100)),
        sa.Column("last_pull_at", sa.DateTime(timezone=True)),
        sa.Column("last_pull_error", sa.String(200)),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("online_order_links")
    op.drop_table("online_order_outbox")
    op.drop_table("stock_reservations")
    op.drop_table("online_orders")
