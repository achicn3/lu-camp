"""add acquisition

Revision ID: 62e9ec3746b1
Revises: c3d8f1a4b6e2
Create Date: 2026-06-05 01:23:09.822886

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "62e9ec3746b1"
down_revision: str | Sequence[str] | None = "c3d8f1a4b6e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 明確命名 FK，使 downgrade 可精準 drop（不靠 server 自動命名）。
_FK_SERIALIZED = "fk_serialized_items_acquisition_id"
_FK_BULK = "fk_bulk_lots_acquisition_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "acquisitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "BUYOUT",
                "CONSIGNMENT",
                "BULK_LOT",
                name="acquisitiontype",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("clerk_user_id", sa.Integer(), nullable=False),
        sa.Column("total_cash_paid", sa.Numeric(precision=12, scale=0), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clerk_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_acquisitions_contact_id"), "acquisitions", ["contact_id"], unique=False
    )
    op.create_index(op.f("ix_acquisitions_store_id"), "acquisitions", ["store_id"], unique=False)
    op.create_foreign_key(_FK_BULK, "bulk_lots", "acquisitions", ["acquisition_id"], ["id"])
    op.create_foreign_key(
        _FK_SERIALIZED, "serialized_items", "acquisitions", ["acquisition_id"], ["id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_FK_SERIALIZED, "serialized_items", type_="foreignkey")
    op.drop_constraint(_FK_BULK, "bulk_lots", type_="foreignkey")
    op.drop_index(op.f("ix_acquisitions_store_id"), table_name="acquisitions")
    op.drop_index(op.f("ix_acquisitions_contact_id"), table_name="acquisitions")
    op.drop_table("acquisitions")
