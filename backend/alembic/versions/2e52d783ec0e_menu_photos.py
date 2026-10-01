"""菜單照片：menu_photos（WebP 成品，以內容雜湊去重）＋ menu_items.photo_sha256（docs/44 §3.4）。

照片存在資料庫（店主 2026-10-02 裁示），每晚備份與還原演練自動涵蓋。

Revision ID: 2e52d783ec0e
Revises: edaa5e5ce783
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "2e52d783ec0e"
down_revision = "edaa5e5ce783"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "menu_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", "sha256", name="uq_menu_photos_store_sha256"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_menu_photos_sha256_hex"),
    )
    op.create_index("ix_menu_photos_store_id", "menu_photos", ["store_id"])
    op.add_column("menu_items", sa.Column("photo_sha256", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_menu_items_photo",
        "menu_items",
        "menu_photos",
        ["store_id", "photo_sha256"],
        ["store_id", "sha256"],
    )


def abort_if_photos_exist(conn: Connection) -> None:
    """照片降版後無處保存；有照片就中止。"""
    n = conn.execute(sa.text("SELECT count(*) FROM menu_photos")).scalar_one()
    if n:
        raise RuntimeError(f"拒絕降版：菜單照片 {n} 張降版後無處保存")


def downgrade() -> None:
    abort_if_photos_exist(op.get_bind())
    op.drop_constraint("fk_menu_items_photo", "menu_items", type_="foreignkey")
    op.drop_column("menu_items", "photo_sha256")
    op.drop_index("ix_menu_photos_store_id", table_name="menu_photos")
    op.drop_table("menu_photos")
