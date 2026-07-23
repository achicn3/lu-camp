"""客顯裝置、POS 櫃檯、可撤銷 device session 與一次性配對。

Revision ID: c9d2e4f6a8b0
Revises: b8a1c4d7e2f9
Create Date: 2026-07-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d2e4f6a8b0"
down_revision: str | Sequence[str] | None = "b8a1c4d7e2f9"
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
    """建立客顯裝置身分與長期配對資料。"""
    op.create_table(
        "pos_terminals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "store_id",
            "installation_id",
            name="uq_pos_terminals_store_installation",
        ),
        sa.UniqueConstraint("id", "store_id", name="uq_pos_terminals_id_store"),
    )
    op.create_index("ix_pos_terminals_store_id", "pos_terminals", ["store_id"])

    op.create_table(
        "kiosk_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("kiosk_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "kiosk_user_id",
            "installation_id",
            name="uq_kiosk_devices_user_installation",
        ),
        sa.UniqueConstraint("id", "store_id", name="uq_kiosk_devices_id_store"),
    )
    op.create_index("ix_kiosk_devices_store_id", "kiosk_devices", ["store_id"])
    op.create_index("ix_kiosk_devices_kiosk_user_id", "kiosk_devices", ["kiosk_user_id"])

    op.create_table(
        "kiosk_device_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("kiosk_device_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_kiosk_device_sessions_device_store",
        ),
        sa.UniqueConstraint("token_hash", name="uq_kiosk_device_sessions_token_hash"),
    )
    op.create_index(
        "ix_kiosk_device_sessions_store_id",
        "kiosk_device_sessions",
        ["store_id"],
    )
    op.create_index(
        "ix_kiosk_device_sessions_kiosk_device_id",
        "kiosk_device_sessions",
        ["kiosk_device_id"],
    )

    op.create_table(
        "kiosk_pairing_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("kiosk_device_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_kiosk_pairing_codes_device_store",
        ),
    )
    op.create_index(
        "ix_kiosk_pairing_codes_store_id",
        "kiosk_pairing_codes",
        ["store_id"],
    )
    op.create_index(
        "ix_kiosk_pairing_codes_kiosk_device_id",
        "kiosk_pairing_codes",
        ["kiosk_device_id"],
    )
    op.create_index(
        "uq_kiosk_pairing_codes_store_active_hash",
        "kiosk_pairing_codes",
        ["store_id", "code_hash"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )

    op.create_table(
        "terminal_kiosk_pairings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("pos_terminal_id", sa.Integer(), nullable=False),
        sa.Column("kiosk_device_id", sa.Integer(), nullable=False),
        sa.Column("paired_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "paired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("unpaired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["pos_terminal_id", "store_id"],
            ["pos_terminals.id", "pos_terminals.store_id"],
            name="fk_terminal_kiosk_pairings_terminal_store",
        ),
        sa.ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_terminal_kiosk_pairings_device_store",
        ),
    )
    op.create_index(
        "ix_terminal_kiosk_pairings_store_id",
        "terminal_kiosk_pairings",
        ["store_id"],
    )
    op.create_index(
        "ix_terminal_kiosk_pairings_pos_terminal_id",
        "terminal_kiosk_pairings",
        ["pos_terminal_id"],
    )
    op.create_index(
        "ix_terminal_kiosk_pairings_kiosk_device_id",
        "terminal_kiosk_pairings",
        ["kiosk_device_id"],
    )
    op.create_index(
        "uq_terminal_kiosk_pairings_active_terminal",
        "terminal_kiosk_pairings",
        ["pos_terminal_id"],
        unique=True,
        postgresql_where=sa.text("unpaired_at IS NULL"),
    )
    op.create_index(
        "uq_terminal_kiosk_pairings_active_device",
        "terminal_kiosk_pairings",
        ["kiosk_device_id"],
        unique=True,
        postgresql_where=sa.text("unpaired_at IS NULL"),
    )


def downgrade() -> None:
    """移除客顯裝置身分與配對資料。"""
    op.drop_index(
        "uq_terminal_kiosk_pairings_active_device",
        table_name="terminal_kiosk_pairings",
    )
    op.drop_index(
        "uq_terminal_kiosk_pairings_active_terminal",
        table_name="terminal_kiosk_pairings",
    )
    op.drop_index(
        "ix_terminal_kiosk_pairings_kiosk_device_id",
        table_name="terminal_kiosk_pairings",
    )
    op.drop_index(
        "ix_terminal_kiosk_pairings_pos_terminal_id",
        table_name="terminal_kiosk_pairings",
    )
    op.drop_index(
        "ix_terminal_kiosk_pairings_store_id",
        table_name="terminal_kiosk_pairings",
    )
    op.drop_table("terminal_kiosk_pairings")

    op.drop_index(
        "uq_kiosk_pairing_codes_store_active_hash",
        table_name="kiosk_pairing_codes",
    )
    op.drop_index(
        "ix_kiosk_pairing_codes_kiosk_device_id",
        table_name="kiosk_pairing_codes",
    )
    op.drop_index("ix_kiosk_pairing_codes_store_id", table_name="kiosk_pairing_codes")
    op.drop_table("kiosk_pairing_codes")

    op.drop_index(
        "ix_kiosk_device_sessions_kiosk_device_id",
        table_name="kiosk_device_sessions",
    )
    op.drop_index(
        "ix_kiosk_device_sessions_store_id",
        table_name="kiosk_device_sessions",
    )
    op.drop_table("kiosk_device_sessions")

    op.drop_index("ix_kiosk_devices_kiosk_user_id", table_name="kiosk_devices")
    op.drop_index("ix_kiosk_devices_store_id", table_name="kiosk_devices")
    op.drop_table("kiosk_devices")

    op.drop_index("ix_pos_terminals_store_id", table_name="pos_terminals")
    op.drop_table("pos_terminals")
