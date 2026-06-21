"""drop campaigns.consignment_discount_bearing (寄售折扣一律按比例分攤)

寄售折扣改為一律按比例分攤（寄售人按折後價分潤），移除 STORE_ABSORBS 選項與此欄（docs/21 §8.1）。
非原生 Enum（CHECK 約束）隨欄位一併移除。

Revision ID: e1f2a3b4c5d6
Revises: d6b2c3e4f5a7
Create Date: 2026-06-21 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d6b2c3e4f5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("campaigns", "consignment_discount_bearing")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "campaigns",
        sa.Column(
            "consignment_discount_bearing",
            sa.Enum(
                "STORE_ABSORBS",
                "PROPORTIONAL",
                name="consignmentdiscountbearing",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            server_default="STORE_ABSORBS",
            nullable=False,
        ),
    )
