"""add sales idempotency_key

Revision ID: 1ee6d8890a79
Revises: 376116e81be6
Create Date: 2026-06-05 23:57:32.665993

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1ee6d8890a79"
down_revision: str | Sequence[str] | None = "376116e81be6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UQ = "uq_sales_store_idempotency_key"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sales", sa.Column("idempotency_key", sa.String(length=80), nullable=True))
    op.add_column(
        "sales", sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(_UQ, "sales", ["store_id", "idempotency_key"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_UQ, "sales", type_="unique")
    op.drop_column("sales", "idempotency_fingerprint")
    op.drop_column("sales", "idempotency_key")
