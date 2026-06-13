"""add sale_tenders and widen payment_method

Revision ID: f1a2b3c4d5e6
Revises: e8f3a7c1d2b9
Create Date: 2026-06-13 12:00:00.000000

SC-3（docs/16 §1.6/§3.2）：銷售多 tender（CASH / STORE_CREDIT），Σ amount = sales.total。
payment_method 摘要欄擴充 STORE_CREDIT / MIXED（native_enum=False → 改寫 CHECK）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.sales.models import (
    SALE_LEDGER_BACKING_DDL,
    SALE_LEDGER_BACKING_DROP_DDL,
    SALE_TENDER_TOTAL_GUARD_DDL,
    SALE_TENDER_TOTAL_GUARD_DROP_DDL,
)

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e8f3a7c1d2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAYMENT_VALUES = ("CASH", "STORE_CREDIT", "MIXED")


def upgrade() -> None:
    """Upgrade schema."""
    # 複合租戶鍵（SC-3 P2）：供 sale_tenders 的 (sale_id, store_id) 複合 FK 綁定。
    op.create_unique_constraint("uq_sales_id_store", "sales", ["id", "store_id"])
    op.create_table(
        "sale_tenders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column(
            "tender_type",
            sa.Enum(
                "CASH",
                "STORE_CREDIT",
                name="tendertype",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 0), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("sale_id", "tender_type", name="uq_sale_tenders_sale_type"),
        sa.CheckConstraint("amount > 0", name="ck_sale_tenders_amount_positive"),
        sa.ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_sale_tenders_sale_tenant",
        ),
    )
    op.create_index("ix_sale_tenders_store_id", "sale_tenders", ["store_id"])
    op.create_index("ix_sale_tenders_sale_id", "sale_tenders", ["sale_id"])

    # payment_method 摘要欄擴充（native_enum=False 的 CHECK 名為 'paymentmethod'）
    op.drop_constraint("paymentmethod", "sales", type_="check")
    allowed = ", ".join(f"'{v}'" for v in _PAYMENT_VALUES)
    op.create_check_constraint("paymentmethod", "sales", f"payment_method IN ({allowed})")

    # 既有銷售回填單一 CASH 收款（新合約：每筆 sale 的 tenders 加總 = total）。
    # total=0 的歷史單不回填（amount>0 CHECK；對平守衛允許 Σ=0=total）。
    op.execute(
        "INSERT INTO sale_tenders (store_id, sale_id, tender_type, amount, created_at, updated_at)"
        " SELECT store_id, id, 'CASH', total, now(), now() FROM sales WHERE total > 0"
    )

    # 收款守衛（DEFERRABLE constraint triggers；回填完成後再安裝，避免回填當下誤擋）：
    # 對平 + 購物金↔帳本雙向綁定。
    for ddl in SALE_TENDER_TOTAL_GUARD_DDL + SALE_LEDGER_BACKING_DDL:
        op.execute(ddl)


def downgrade() -> None:
    """Downgrade schema."""
    for ddl in SALE_LEDGER_BACKING_DROP_DDL + SALE_TENDER_TOTAL_GUARD_DROP_DDL:
        op.execute(ddl)
    op.drop_constraint("paymentmethod", "sales", type_="check")
    op.create_check_constraint("paymentmethod", "sales", "payment_method IN ('CASH')")
    op.drop_index("ix_sale_tenders_sale_id", "sale_tenders")
    op.drop_index("ix_sale_tenders_store_id", "sale_tenders")
    op.drop_table("sale_tenders")
    op.drop_constraint("uq_sales_id_store", "sales", type_="unique")
