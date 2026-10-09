"""「不知道喝什麼」引導推薦（docs/63 §2 M2a；店主 2026-10-09）。

- 新表 online_menu_quizzes：每店一份題目文件（題目、答案、每個答案勾的品項）與啟用開關。

Revision ID: a4c8e2f6b0d3
Revises: f3b7d9e1a5c2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a4c8e2f6b0d3"
down_revision = "f3b7d9e1a5c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "online_menu_quizzes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("questions", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", name="online_menu_quizzes_store_id_key"),
    )


def downgrade() -> None:
    # 只是呈現設定（題目與勾選），降版刪掉不影響任何帳。
    op.drop_table("online_menu_quizzes")
