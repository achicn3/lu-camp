"""線上點餐店內端：發佈紀錄、桌位碼、已推送媒體（docs/44 §5.3；O3b）。

Revision ID: da91ffee580d
Revises: 2e52d783ec0e
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "da91ffee580d"
down_revision = "2e52d783ec0e"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[Any]]:
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
        "online_menu_publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("store_id", "version", name="uq_online_menu_publications_version"),
    )
    op.create_index(
        "ix_online_menu_publications_store_id", "online_menu_publications", ["store_id"]
    )
    op.create_table(
        "online_table_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("label", sa.String(20), nullable=False),
        sa.Column("service_mode", sa.String(10), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("code", name="uq_online_table_codes_code"),
        sa.CheckConstraint(
            "service_mode IN ('DINE_IN', 'TAKEOUT')", name="ck_online_table_codes_mode"
        ),
    )
    op.create_index("ix_online_table_codes_store_id", "online_table_codes", ["store_id"])
    op.create_index(
        "uq_online_table_codes_active_label",
        "online_table_codes",
        ["store_id", "label"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "online_pushed_media",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("store_id", "kind", "sha256", name="uq_online_pushed_media"),
        sa.CheckConstraint("kind IN ('PHOTO', 'FONT')", name="ck_online_pushed_media_kind"),
    )
    op.create_index("ix_online_pushed_media_store_id", "online_pushed_media", ["store_id"])


def abort_if_online_data_exists(conn: Connection) -> None:
    """桌位碼印在桌上、發佈紀錄是對帳依據；有資料就拒絕降版。"""
    found = []
    for table, label in (
        ("online_table_codes", "桌位碼"),
        ("online_menu_publications", "發佈紀錄"),
    ):
        n = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if n:
            found.append(f"{label} {n} 筆")
    if found:
        raise RuntimeError("拒絕降版：以下資料降版後無處保存——" + "、".join(found))


def downgrade() -> None:
    abort_if_online_data_exists(op.get_bind())
    op.drop_table("online_pushed_media")
    op.drop_index("uq_online_table_codes_active_label", table_name="online_table_codes")
    op.drop_table("online_table_codes")
    op.drop_table("online_menu_publications")
