"""sale_line campaign discount fields (門市活動折扣留痕)

Revision ID: d6b2c3e4f5a7
Revises: c5a1d2e3f4b6
Create Date: 2026-06-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6b2c3e4f5a7"
down_revision: str | Sequence[str] | None = "c5a1d2e3f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "sale_lines",
        sa.Column("original_unit_price", sa.Numeric(precision=12, scale=0), nullable=True),
    )
    op.add_column(
        "sale_lines",
        sa.Column(
            "discount_amount",
            sa.Numeric(precision=12, scale=0),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("sale_lines", sa.Column("campaign_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sale_lines_campaign_id", "sale_lines", "campaigns", ["campaign_id"], ["id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_sale_lines_campaign_id", "sale_lines", type_="foreignkey")
    op.drop_column("sale_lines", "campaign_id")
    op.drop_column("sale_lines", "discount_amount")
    op.drop_column("sale_lines", "original_unit_price")
