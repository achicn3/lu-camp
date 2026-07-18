"""行動支付：enum 擴充 LINE_PAY/TAIWAN_PAY + sale_tenders.fee_amount + settings 費率

新增非現金付款方式（docs/30）：
- sale_tenders.tender_type / sales.payment_method 的 CHECK 擴充 LINE_PAY、TAIWAN_PAY
  （_enum_col native_enum=False → CHECK 名 tendertype / paymentmethod）。
- sale_tenders.fee_amount：支付手續費（店家成本，整數元，預設 0；現金/購物金為 0）。
- settings：linepay_enabled、linepay_fee_pct、taiwanpay_fee_pct（費率小數，同 tax_rate 慣例）。

Revision ID: c7d8e9f0a1b2
Revises: a2b3c4d5e6f7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENDER_OLD = ("CASH", "STORE_CREDIT")
_TENDER_NEW = ("CASH", "STORE_CREDIT", "LINE_PAY", "TAIWAN_PAY")
_PAYMENT_OLD = ("CASH", "STORE_CREDIT", "MIXED")
_PAYMENT_NEW = ("CASH", "STORE_CREDIT", "LINE_PAY", "TAIWAN_PAY", "MIXED")


def _reset_check(constraint: str, table: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(constraint, table, type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(constraint, table, f"{column} IN ({allowed})")


def upgrade() -> None:
    _reset_check("tendertype", "sale_tenders", "tender_type", _TENDER_NEW)
    _reset_check("paymentmethod", "sales", "payment_method", _PAYMENT_NEW)
    op.add_column(
        "sale_tenders",
        sa.Column(
            "fee_amount", sa.Numeric(12, 0), nullable=False, server_default=sa.text("0")
        ),
    )
    for col in ("linepay_enabled",):
        op.add_column(
            "settings",
            sa.Column(col, sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    for col in ("linepay_fee_pct", "taiwanpay_fee_pct"):
        op.add_column(
            "settings",
            sa.Column(col, sa.Numeric(5, 4), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    op.drop_column("settings", "taiwanpay_fee_pct")
    op.drop_column("settings", "linepay_fee_pct")
    op.drop_column("settings", "linepay_enabled")
    op.drop_column("sale_tenders", "fee_amount")
    # CHECK 收窄：僅在無 LINE_PAY/TAIWAN_PAY 資料時可行（有資料應 roll forward，不強制降級）。
    _reset_check("paymentmethod", "sales", "payment_method", _PAYMENT_OLD)
    _reset_check("tendertype", "sale_tenders", "tender_type", _TENDER_OLD)
