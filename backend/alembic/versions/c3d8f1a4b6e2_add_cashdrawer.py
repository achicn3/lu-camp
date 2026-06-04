"""add cashdrawer

Revision ID: c3d8f1a4b6e2
Revises: 6941c06a8cab
Create Date: 2026-06-04 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d8f1a4b6e2"
down_revision: str | Sequence[str] | None = "6941c06a8cab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cash_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("opened_by", sa.Integer(), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("opening_float", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "CLOSED",
                name="cashsessionstatus",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            server_default="OPEN",
            nullable=False,
        ),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counted_amount", sa.Numeric(precision=12, scale=0), nullable=True),
        sa.Column("expected_amount", sa.Numeric(precision=12, scale=0), nullable=True),
        sa.Column("variance", sa.Numeric(precision=12, scale=0), nullable=True),
        sa.ForeignKeyConstraint(
            ["closed_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["opened_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cash_sessions_store_id"), "cash_sessions", ["store_id"], unique=False)
    op.create_index(
        "uq_one_open_cash_session_per_store",
        "cash_sessions",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_table(
        "cash_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "SALE_IN",
                "BUYOUT_OUT",
                "CONSIGNMENT_PAYOUT_OUT",
                "MANUAL_ADJUST",
                name="cashmovementtype",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("ref_type", sa.String(length=50), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["cash_sessions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cash_movements_session_id"), "cash_movements", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_cash_movements_store_id"), "cash_movements", ["store_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_cash_movements_store_id"), table_name="cash_movements")
    op.drop_index(op.f("ix_cash_movements_session_id"), table_name="cash_movements")
    op.drop_table("cash_movements")
    op.drop_index("uq_one_open_cash_session_per_store", table_name="cash_sessions")
    op.drop_index(op.f("ix_cash_sessions_store_id"), table_name="cash_sessions")
    op.drop_table("cash_sessions")
