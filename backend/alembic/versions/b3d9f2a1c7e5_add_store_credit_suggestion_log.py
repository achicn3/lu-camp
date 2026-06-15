"""add store_credit_suggestion_log

Revision ID: b3d9f2a1c7e5
Revises: a7c4e9f1b2d8
Create Date: 2026-06-15 09:00:00.000000

SC-5b（docs/16 §1.4/§6.2）：新表 store_credit_suggestion_log 落庫每店每日的溢價建議快照。
每店每日唯一鍵提供 lazy 計算的冪等落庫；本表為可重算的衍生資料，不設不可變 trigger。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b3d9f2a1c7e5"
down_revision: str | Sequence[str] | None = "a7c4e9f1b2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "store_credit_suggestion_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("for_date", sa.Date(), nullable=False),
        sa.Column("window_metrics", JSONB, nullable=False),
        sa.Column("constraint_values", JSONB, nullable=False),
        sa.Column("suggested_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("engine_version", sa.String(40), nullable=False),
        sa.Column(
            "insufficient_data", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", "for_date", name="uq_store_credit_suggestion_log_day"),
    )
    op.create_index(
        "ix_store_credit_suggestion_log_store_id", "store_credit_suggestion_log", ["store_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_store_credit_suggestion_log_store_id", "store_credit_suggestion_log"
    )
    op.drop_table("store_credit_suggestion_log")
