"""客顯即時購物車 session、版本與 append-only 事件。

Revision ID: d0e1f2a3b4c5
Revises: c9d2e4f6a8b0
Create Date: 2026-07-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.modules.customerdisplay.models import (
    CART_SESSION_EVENT_IMMUTABLE_DDL,
    CART_SESSION_EVENT_IMMUTABLE_DROP_DDL,
)
from app.shared.enums import CartSessionStatus

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d2e4f6a8b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    """建立伺服器權威購物車及其不可變版本事件。"""
    op.create_table(
        "cart_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("pos_terminal_id", sa.Integer(), nullable=False),
        sa.Column("kiosk_device_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                CartSessionStatus,
                native_enum=False,
                length=30,
                create_constraint=True,
                name="cartsessionstatus",
            ),
            server_default=CartSessionStatus.DRAFT.value,
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("buyer_contact_id", sa.Integer(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "last_changes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["pos_terminal_id", "store_id"],
            ["pos_terminals.id", "pos_terminals.store_id"],
            name="fk_cart_sessions_terminal_store",
        ),
        sa.ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_cart_sessions_device_store",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_cart_sessions_buyer_store",
        ),
        sa.UniqueConstraint("id", "store_id", name="uq_cart_sessions_id_store"),
    )
    op.create_index("ix_cart_sessions_store_id", "cart_sessions", ["store_id"])
    op.create_index(
        "ix_cart_sessions_pos_terminal_id",
        "cart_sessions",
        ["pos_terminal_id"],
    )
    op.create_index(
        "ix_cart_sessions_kiosk_device_id",
        "cart_sessions",
        ["kiosk_device_id"],
    )
    op.create_index(
        "ix_cart_sessions_buyer_contact_id",
        "cart_sessions",
        ["buyer_contact_id"],
    )
    op.create_index(
        "uq_cart_sessions_active_terminal",
        "cart_sessions",
        ["pos_terminal_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('DRAFT','FROZEN','PROCESSING','PAYMENT_UNCERTAIN')"),
    )
    op.create_index(
        "uq_cart_sessions_active_device",
        "cart_sessions",
        ["kiosk_device_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('DRAFT','FROZEN','PROCESSING','PAYMENT_UNCERTAIN')"),
    )

    op.create_table(
        "cart_session_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("cart_session_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cart_session_id", "store_id"],
            ["cart_sessions.id", "cart_sessions.store_id"],
            name="fk_cart_session_events_session_store",
        ),
        sa.UniqueConstraint(
            "cart_session_id",
            "revision",
            name="uq_cart_session_events_session_revision",
        ),
    )
    op.create_index(
        "ix_cart_session_events_store_id",
        "cart_session_events",
        ["store_id"],
    )
    op.create_index(
        "ix_cart_session_events_cart_session_id",
        "cart_session_events",
        ["cart_session_id"],
    )
    for ddl in CART_SESSION_EVENT_IMMUTABLE_DDL:
        op.execute(ddl)


def downgrade() -> None:
    """移除客顯購物車與事件資料。"""
    for ddl in CART_SESSION_EVENT_IMMUTABLE_DROP_DDL:
        op.execute(ddl)
    op.drop_index(
        "ix_cart_session_events_cart_session_id",
        table_name="cart_session_events",
    )
    op.drop_index(
        "ix_cart_session_events_store_id",
        table_name="cart_session_events",
    )
    op.drop_table("cart_session_events")

    op.drop_index("uq_cart_sessions_active_device", table_name="cart_sessions")
    op.drop_index("uq_cart_sessions_active_terminal", table_name="cart_sessions")
    op.drop_index("ix_cart_sessions_buyer_contact_id", table_name="cart_sessions")
    op.drop_index("ix_cart_sessions_kiosk_device_id", table_name="cart_sessions")
    op.drop_index("ix_cart_sessions_pos_terminal_id", table_name="cart_sessions")
    op.drop_index("ix_cart_sessions_store_id", table_name="cart_sessions")
    op.drop_table("cart_sessions")
