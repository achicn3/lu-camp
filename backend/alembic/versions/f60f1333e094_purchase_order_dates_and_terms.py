"""行事曆 1B：採購單日期與供應商付款條件（docs/99 §5、ADR-034）。

- suppliers：payment_terms、payment_terms_value（預設付款條件，限管理者設定）。
- purchase_orders：ordered_on（實際下單日）、expected_on（預計到貨日）、
  payment_terms*（送出時的快照）、
  payment_due_override（管理者手動改的付款期限）。
- 已送出的舊單以送出時間的台北日期補上 ordered_on（草稿與從草稿取消的單沒有下單日）。

降版：只要有店員填過的資料（預計到貨日、付款條件、手動期限、改過的下單日）就拒絕。

Revision ID: f60f1333e094
Revises: d7a95030120c
"""

import sqlalchemy as sa
from alembic import op

revision = "f60f1333e094"
down_revision = "d7a95030120c"
branch_labels = None
depends_on = None

_TERMS = ("PREPAID", "COD", "MONTHLY", "NET_DAYS")
_TERMS_CHECK = (
    "(payment_terms IS NULL AND payment_terms_value IS NULL)"
    " OR (payment_terms IN ('PREPAID', 'COD') AND payment_terms_value IS NULL)"
    " OR (payment_terms = 'MONTHLY' AND payment_terms_value BETWEEN 1 AND 31)"
    " OR (payment_terms = 'NET_DAYS' AND payment_terms_value BETWEEN 0 AND 365)"
)


def _terms_column() -> sa.Column[str]:
    return sa.Column(
        "payment_terms",
        sa.Enum(*_TERMS, native_enum=False, length=30, create_constraint=True),
        nullable=True,
    )


def upgrade() -> None:
    op.add_column("suppliers", _terms_column())
    op.add_column("suppliers", sa.Column("payment_terms_value", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_suppliers_payment_terms", "suppliers", _TERMS_CHECK)

    op.add_column("purchase_orders", sa.Column("ordered_on", sa.Date(), nullable=True))
    op.add_column("purchase_orders", sa.Column("expected_on", sa.Date(), nullable=True))
    op.add_column("purchase_orders", _terms_column())
    op.add_column("purchase_orders", sa.Column("payment_terms_value", sa.Integer(), nullable=True))
    op.add_column("purchase_orders", sa.Column("payment_due_override", sa.Date(), nullable=True))
    op.create_check_constraint("ck_purchase_orders_payment_terms", "purchase_orders", _TERMS_CHECK)
    op.execute(
        "UPDATE purchase_orders SET ordered_on = (ordered_at AT TIME ZONE 'Asia/Taipei')::date "
        "WHERE status IN ('ORDERED', 'PARTIAL', 'RECEIVED')"
    )


def downgrade() -> None:
    entered = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM purchase_orders WHERE expected_on IS NOT NULL"
                " OR payment_terms IS NOT NULL OR payment_due_override IS NOT NULL"
                " OR ordered_on IS DISTINCT FROM CASE"
                " WHEN status IN ('ORDERED', 'PARTIAL', 'RECEIVED')"
                " THEN (ordered_at AT TIME ZONE 'Asia/Taipei')::date END)"
                " OR EXISTS (SELECT 1 FROM suppliers WHERE payment_terms IS NOT NULL)"
            )
        )
        .scalar()
    )
    if entered:
        raise RuntimeError(
            "已有採購單日期或付款條件的資料，降版會整批消失；請只退程式、不降 schema"
        )
    op.drop_constraint("ck_purchase_orders_payment_terms", "purchase_orders", type_="check")
    for column in (
        "payment_due_override",
        "payment_terms_value",
        "payment_terms",
        "expected_on",
        "ordered_on",
    ):
        op.drop_column("purchase_orders", column)
    op.drop_constraint("ck_suppliers_payment_terms", "suppliers", type_="check")
    op.drop_column("suppliers", "payment_terms_value")
    op.drop_column("suppliers", "payment_terms")
