"""開店前檢查：自訂項目與每日狀態

2026-09-17 裁示：每天第一次打開系統要跳出開店前檢查，全部綠燈才消失。
狀態以「每店每日」為單位（任何一台裝置完成就算完成），所以存後端不是瀏覽器。

`opening_checks.done_item_ids` 用陣列而非關聯表：一天就那幾項，讀寫都是整列，
關聯表只會多一張表要維護。Postgres 陣列不能掛外鍵，讀取時與現存項目取交集。

Revision ID: d7c3e5b19a20
Revises: c5e2a9d47f10
Create Date: 2026-09-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7c3e5b19a20"
down_revision: str | Sequence[str] | None = "c5e2a9d47f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "opening_check_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("href", sa.String(length=200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_opening_check_items_store_id", "opening_check_items", ["store_id"], unique=False
    )
    op.create_table(
        "opening_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column(
            "done_item_ids",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "skipped_keys",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("store_id", "business_date", name="uq_opening_checks_store_date"),
    )
    op.create_index("ix_opening_checks_store_id", "opening_checks", ["store_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    只是檢查紀錄，沒有金流或法律效力，直接刪表；店主自訂的項目會一併消失。
    """
    op.drop_index("ix_opening_checks_store_id", table_name="opening_checks")
    op.drop_table("opening_checks")
    op.drop_index("ix_opening_check_items_store_id", table_name="opening_check_items")
    op.drop_table("opening_check_items")
