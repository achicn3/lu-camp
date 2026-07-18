"""備份系統（docs/31）：backup_runs / restore_runs 狀態表 + settings 備份設定欄

- backup_runs：每次備份一列；部分唯一索引（status=RUNNING）確保同店至多一列進行中（單一在跑守衛）。
- restore_runs：每次還原一列（高危留痕）。
- settings：backup_enabled / backup_interval_hours / backup_retention / backup_offpeak_hour。
status 以 _enum_col native_enum=False → CHECK（名 backupstatus / backuptrigger / restorestatus）。

Revision ID: d4b8f1a2c3e5
Revises: e2a9c4b7f1d3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4b8f1a2c3e5"
down_revision: str | None = "e2a9c4b7f1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER = ("SCHEDULED", "MANUAL")
_BSTATUS = ("RUNNING", "SUCCEEDED", "FAILED")
_RSTATUS = ("RUNNING", "VERIFIED", "FAILED")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "backup_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "backup_interval_hours", sa.Integer(), nullable=False, server_default=sa.text("24")
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "backup_retention", sa.Integer(), nullable=False, server_default=sa.text("30")
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "backup_offpeak_hour", sa.Integer(), nullable=False, server_default=sa.text("4")
        ),
    )

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("db_name", sa.String(63), nullable=False),
        sa.Column("file_name", sa.String(200), nullable=True),
        sa.Column("r2_key", sa.String(300), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.CheckConstraint(f"trigger IN ({_in(_TRIGGER)})", name="backuptrigger"),
        sa.CheckConstraint(f"status IN ({_in(_BSTATUS)})", name="backupstatus"),
    )
    op.create_index("ix_backup_runs_store_id", "backup_runs", ["store_id"])
    # 單一在跑守衛：同店至多一列 RUNNING。
    op.create_index(
        "uq_backup_runs_one_running",
        "backup_runs",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )

    op.create_table(
        "restore_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_r2_key", sa.String(300), nullable=False),
        sa.Column("restore_db_name", sa.String(63), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verifications", postgresql.JSONB(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.CheckConstraint(f"status IN ({_in(_RSTATUS)})", name="restorestatus"),
    )
    op.create_index("ix_restore_runs_store_id", "restore_runs", ["store_id"])


def downgrade() -> None:
    op.drop_index("ix_restore_runs_store_id", table_name="restore_runs")
    op.drop_table("restore_runs")
    op.drop_index("uq_backup_runs_one_running", table_name="backup_runs")
    op.drop_index("ix_backup_runs_store_id", table_name="backup_runs")
    op.drop_table("backup_runs")
    op.drop_column("settings", "backup_offpeak_hour")
    op.drop_column("settings", "backup_retention")
    op.drop_column("settings", "backup_interval_hours")
    op.drop_column("settings", "backup_enabled")
