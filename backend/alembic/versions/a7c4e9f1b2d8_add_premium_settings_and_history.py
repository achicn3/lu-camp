"""add premium settings and premium_rate_history

Revision ID: a7c4e9f1b2d8
Revises: f1a2b3c4d5e6
Create Date: 2026-06-14 10:00:00.000000

SC-5a（docs/16 §1.3/§1.5/§6.1）：settings 加 premium_rate_min/max、monthly_fixed_cash_outflow、
store_credit_engine_params（JSONB）；新表 premium_rate_history 留痕溢價率變更。
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.modules.settings.defaults import DEFAULT_STORE_CREDIT_ENGINE_PARAMS

revision: str = "a7c4e9f1b2d8"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENGINE_PARAMS_DEFAULT = f"'{json.dumps(DEFAULT_STORE_CREDIT_ENGINE_PARAMS)}'::jsonb"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "settings",
        sa.Column(
            "premium_rate_min", sa.Numeric(5, 4), server_default=sa.text("0.0000"), nullable=False
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "premium_rate_max", sa.Numeric(5, 4), server_default=sa.text("0.2000"), nullable=False
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "monthly_fixed_cash_outflow",
            sa.Numeric(12, 0),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "store_credit_engine_params",
            JSONB,
            server_default=sa.text(_ENGINE_PARAMS_DEFAULT),
            nullable=False,
        ),
    )

    op.create_table(
        "premium_rate_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("old_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("new_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column("suggested_rate_at_change", sa.Numeric(5, 4), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
    )
    op.create_index("ix_premium_rate_history_store_id", "premium_rate_history", ["store_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_premium_rate_history_store_id", "premium_rate_history")
    op.drop_table("premium_rate_history")
    op.drop_column("settings", "store_credit_engine_params")
    op.drop_column("settings", "monthly_fixed_cash_outflow")
    op.drop_column("settings", "premium_rate_max")
    op.drop_column("settings", "premium_rate_min")
