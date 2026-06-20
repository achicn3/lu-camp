"""f6.5 acquisition void: void columns + ACQUISITION_VOID_IN cash type

Revision ID: e8c70a9e0447
Revises: f6a7b8c9d0e1
Create Date: 2026-06-18 19:32:00.049228

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8c70a9e0447"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_VOIDED_BY = "fk_acquisitions_voided_by_users"

# cash_movements.type 為 native_enum=False（VARCHAR + CHECK 名 cashmovementtype）；
# 新增列舉值須擴充 CHECK，否則插入 ACQUISITION_VOID_IN 會被擋。
_CASH_TYPES_OLD = ("SALE_IN", "BUYOUT_OUT", "CONSIGNMENT_PAYOUT_OUT", "MANUAL_ADJUST")
_CASH_TYPES_NEW = (*_CASH_TYPES_OLD, "ACQUISITION_VOID_IN")


def _check_clause(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"(type)::text = ANY (ARRAY[{joined}]::text[])"


def upgrade() -> None:
    """Upgrade schema."""
    # F6.5 作廢欄（additive、nullable）
    op.add_column("acquisitions", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("acquisitions", sa.Column("voided_by", sa.Integer(), nullable=True))
    op.add_column("acquisitions", sa.Column("void_reason", sa.String(length=500), nullable=True))
    op.create_foreign_key(_FK_VOIDED_BY, "acquisitions", "users", ["voided_by"], ["id"])

    # 擴充 cash_movements 的型別 CHECK 以納入 ACQUISITION_VOID_IN
    op.drop_constraint("cashmovementtype", "cash_movements", type_="check")
    op.create_check_constraint("cashmovementtype", "cash_movements", _check_clause(_CASH_TYPES_NEW))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("cashmovementtype", "cash_movements", type_="check")
    op.create_check_constraint("cashmovementtype", "cash_movements", _check_clause(_CASH_TYPES_OLD))

    op.drop_constraint(_FK_VOIDED_BY, "acquisitions", type_="foreignkey")
    op.drop_column("acquisitions", "void_reason")
    op.drop_column("acquisitions", "voided_by")
    op.drop_column("acquisitions", "voided_at")
