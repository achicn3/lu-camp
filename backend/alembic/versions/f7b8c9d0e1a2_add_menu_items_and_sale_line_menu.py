"""add menu_items and sale_lines.menu_item_id (餐飲/內用菜單)

新增餐飲菜單品項表 menu_items；sale_lines 加 menu_item_id 外鍵並把 line_type 的 CHECK
（native_enum=False，約束名 'salelinetype'）擴充 'MENU'。

Revision ID: f7b8c9d0e1a2
Revises: e1f2a3b4c5d6
Create Date: 2026-06-22 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b8c9d0e1a2"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LINE_TYPES = "'SERIALIZED', 'CATALOG', 'BULK_LOT'"
_NEW_LINE_TYPES = "'SERIALIZED', 'CATALOG', 'BULK_LOT', 'MENU'"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "menu_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False, index=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column(
            "is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.add_column("sale_lines", sa.Column("menu_item_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sale_lines_menu_item_id", "sale_lines", "menu_items", ["menu_item_id"], ["id"]
    )

    # 擴充 line_type 的 CHECK（native_enum=False 的約束名為 'salelinetype'）。
    op.drop_constraint("salelinetype", "sale_lines", type_="check")
    op.create_check_constraint(
        "salelinetype", "sale_lines", f"line_type IN ({_NEW_LINE_TYPES})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("salelinetype", "sale_lines", type_="check")
    op.create_check_constraint(
        "salelinetype", "sale_lines", f"line_type IN ({_OLD_LINE_TYPES})"
    )
    op.drop_constraint("fk_sale_lines_menu_item_id", "sale_lines", type_="foreignkey")
    op.drop_column("sale_lines", "menu_item_id")
    op.drop_table("menu_items")
