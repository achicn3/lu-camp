"""線上點餐人氣標籤設定（docs/63 §7 M2b；店主 2026-10-10）。

- 新表 online_menu_popularity：每店一列（開關、天數、門檻份數）；沒有列＝預設（開、30 天、10 份）。

Revision ID: b6d0f4a8c2e5
Revises: a4c8e2f6b0d3
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d0f4a8c2e5"
down_revision = "a4c8e2f6b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "online_menu_popularity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("window_days", sa.Integer(), server_default=sa.text("30"), nullable=False),
        sa.Column("min_qty", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", name="online_menu_popularity_store_id_key"),
        sa.CheckConstraint(
            "window_days IN (7, 14, 30, 60, 90)", name="ck_online_menu_popularity_window"
        ),
        sa.CheckConstraint("min_qty BETWEEN 1 AND 999", name="ck_online_menu_popularity_min_qty"),
    )


def downgrade() -> None:
    # 只是顯示設定，降版刪掉不影響任何帳。
    op.drop_table("online_menu_popularity")
