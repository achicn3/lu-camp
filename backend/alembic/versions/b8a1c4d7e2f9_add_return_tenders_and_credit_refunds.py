"""add auditable return tenders and store-credit refunds

Revision ID: b8a1c4d7e2f9
Revises: a6c4e2f8b1d3
Create Date: 2026-07-23

退貨按原付款渠道拆帳；購物金優先回補，外部渠道只退差額。退款渠道、退貨明細與
購物金 REFUND/SALE_RETURN 帳本以 deferred constraint triggers 雙向對平。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.returns.models import (
    RETURN_LEDGER_GUARD_DDL,
    RETURN_LEDGER_GUARD_DROP_DDL,
    RETURN_TENDER_GUARD_DDL,
    RETURN_TENDER_GUARD_DROP_DDL,
)

revision: str = "b8a1c4d7e2f9"
down_revision: str | None = "a6c4e2f8b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTRY_TYPES = ("CREDIT", "DEBIT", "REFUND", "REVERSAL", "ADJUSTMENT")
_OLD_ENTRY_TYPES = ("CREDIT", "DEBIT", "REVERSAL", "ADJUSTMENT")
_SOURCE_TYPES = (
    "ACQUISITION",
    "SALE",
    "SALE_RETURN",
    "SALE_VOID",
    "ACQUISITION_ROLLBACK",
    "MANUAL",
)
_OLD_SOURCE_TYPES = (
    "ACQUISITION",
    "SALE",
    "SALE_VOID",
    "ACQUISITION_ROLLBACK",
    "MANUAL",
)


def _enum_check(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint("storecreditentrytype", "store_credit_ledger", type_="check")
    op.drop_constraint("storecreditsourcetype", "store_credit_ledger", type_="check")
    op.create_check_constraint(
        "storecreditentrytype", "store_credit_ledger", _enum_check("entry_type", _ENTRY_TYPES)
    )
    op.create_check_constraint(
        "storecreditsourcetype",
        "store_credit_ledger",
        _enum_check("source_type", _SOURCE_TYPES),
    )
    op.create_check_constraint(
        "ck_scl_refund_pos",
        "store_credit_ledger",
        "entry_type <> 'REFUND' OR signed_amount > 0",
    )
    op.create_check_constraint(
        "ck_scl_refund_source",
        "store_credit_ledger",
        "entry_type <> 'REFUND' OR source_type = 'SALE_RETURN'",
    )
    op.create_check_constraint("ck_returns_refund_amount_positive", "returns", "refund_amount > 0")

    op.create_table(
        "return_tenders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("return_id", sa.Integer(), nullable=False),
        sa.Column(
            "tender_type",
            sa.Enum(
                "CASH",
                "STORE_CREDIT",
                "TAIWAN_PAY",
                "LINE_PAY",
                name="tendertype",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 0), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("return_id", "tender_type", name="uq_return_tenders_return_type"),
        sa.CheckConstraint("amount > 0", name="ck_return_tenders_amount_positive"),
        sa.ForeignKeyConstraint(
            ["return_id", "store_id"],
            ["returns.id", "returns.store_id"],
            ondelete="CASCADE",
            name="fk_return_tenders_return_store",
        ),
    )
    op.create_index("ix_return_tenders_store_id", "return_tenders", ["store_id"])
    op.create_index("ix_return_tenders_return_id", "return_tenders", ["return_id"])

    # 上線前既有退貨只可能是舊流程允許的純現金或純 LINE Pay；依原銷售 tender 回填。
    op.execute(
        """
        INSERT INTO return_tenders (
            store_id, return_id, tender_type, amount, created_at, updated_at
        )
        SELECT r.store_id,
               r.id,
               CASE WHEN EXISTS (
                   SELECT 1 FROM sale_tenders st
                    WHERE st.sale_id = r.sale_id AND st.tender_type = 'LINE_PAY'
               ) THEN 'LINE_PAY' ELSE 'CASH' END,
               r.refund_amount,
               r.created_at,
               r.created_at
          FROM returns r
        """
    )

    for ddl in RETURN_TENDER_GUARD_DDL:
        op.execute(ddl)
    for ddl in RETURN_LEDGER_GUARD_DDL:
        op.execute(ddl)


def downgrade() -> None:
    # 舊 schema 無法表達購物金回補或台灣Pay 退貨；有資料時拒絕有損降版。
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM store_credit_ledger
             WHERE entry_type = 'REFUND' OR source_type = 'SALE_RETURN'
          ) OR EXISTS (
            SELECT 1 FROM return_tenders WHERE tender_type = 'TAIWAN_PAY'
          ) THEN
            RAISE EXCEPTION '存在舊版無法表達的購物金或台灣Pay退貨資料，無法安全降版';
          END IF;
        END
        $$
        """
    )
    for ddl in RETURN_LEDGER_GUARD_DROP_DDL:
        op.execute(ddl)
    for ddl in RETURN_TENDER_GUARD_DROP_DDL:
        op.execute(ddl)
    op.drop_index("ix_return_tenders_return_id", table_name="return_tenders")
    op.drop_index("ix_return_tenders_store_id", table_name="return_tenders")
    op.drop_table("return_tenders")
    op.drop_constraint("ck_returns_refund_amount_positive", "returns", type_="check")
    op.drop_constraint("ck_scl_refund_source", "store_credit_ledger", type_="check")
    op.drop_constraint("ck_scl_refund_pos", "store_credit_ledger", type_="check")
    op.drop_constraint("storecreditsourcetype", "store_credit_ledger", type_="check")
    op.drop_constraint("storecreditentrytype", "store_credit_ledger", type_="check")
    op.create_check_constraint(
        "storecreditentrytype",
        "store_credit_ledger",
        _enum_check("entry_type", _OLD_ENTRY_TYPES),
    )
    op.create_check_constraint(
        "storecreditsourcetype",
        "store_credit_ledger",
        _enum_check("source_type", _OLD_SOURCE_TYPES),
    )
