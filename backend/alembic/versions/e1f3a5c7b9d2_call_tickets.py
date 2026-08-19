"""叫號系統：call_tickets（docs/38）

收購前的候位清單：客人填表單、把連結傳來，店家登記後取號，處理完按完成。

**手寫而非 autogenerate 原樣採用**：autogenerate 會夾帶 linepay/sale_tenders 等既有漂移，
把不相干的 alter_column 混進本次 migration。只保留本功能的變更。

Revision ID: e1f3a5c7b9d2
Revises: d8b2f4a6c0e3
Create Date: 2026-08-19 08:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f3a5c7b9d2"
down_revision: str | Sequence[str] | None = "d8b2f4a6c0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 與 `app.shared.enums.CallTicketStatus` 同步。
_STATUSES = ("WAITING", "DONE")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "call_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        # **台北營業日**（core.time.store_date），每日重置的依據。
        sa.Column("ticket_date", sa.Date(), nullable=False),
        sa.Column("ticket_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*_STATUSES, name="callticketstatus", native_enum=False, length=16),
            server_default="WAITING",
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("completed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["completed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_call_tickets_store_id"), "call_tickets", ["store_id"])
    op.create_index("ix_call_tickets_store_status", "call_tickets", ["store_id", "status"])
    # 同店同日號碼唯一——並發配號撞號時的最後防線。
    op.create_index(
        "uq_call_tickets_store_date_no",
        "call_tickets",
        ["store_id", "ticket_date", "ticket_no"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_call_tickets_store_date_no", table_name="call_tickets")
    op.drop_index("ix_call_tickets_store_status", table_name="call_tickets")
    op.drop_index(op.f("ix_call_tickets_store_id"), table_name="call_tickets")
    op.drop_table("call_tickets")
