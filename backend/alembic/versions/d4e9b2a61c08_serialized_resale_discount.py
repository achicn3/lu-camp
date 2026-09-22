"""Persist the resale discount selected during acquisition."""

import sqlalchemy as sa
from alembic import op

revision = "d4e9b2a61c08"
down_revision = "c3f8a1d40be7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("serialized_items", sa.Column("resale_discount_pct", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_serialized_resale_discount", "serialized_items", "resale_discount_pct BETWEEN 1 AND 100"
    )


def downgrade() -> None:
    op.drop_constraint("ck_serialized_resale_discount", "serialized_items", type_="check")
    op.drop_column("serialized_items", "resale_discount_pct")
