"""拆分銷售與發票的生命週期：sales.status 新增 VOIDED、invoices 新增 void_reason

原設計把「這筆銷售是否作廢」記在 `sales.invoice_status = VOID`——但那是**發票**的狀態。
電子發票關閉時該筆交易根本沒有發票，卻仍被標成「發票已作廢」；報表與清單也因此以
`invoice_status != VOID` 過濾已作廢銷售，語意錯置。

本次遷移把兩件事分開：
- `sales.status` 新增 **VOIDED**（銷售自身的生命週期，作廢與否的唯一事實來源）
- `invoices.void_reason` 新增（SALE_VOID / FULL_RETURN / CORRECTION）——同樣是作廢，
  帳務意義不同，必須可分辨

**回填無歧義**：在本次遷移之前，`invoice_status = 'VOID'` 只可能由 `void_sale()` 產生
（退貨路徑當時只會開折讓、不會作廢發票），因此 `invoice_status='VOID'` ⟺ 該筆銷售已作廢，
一對一對應。等到「退貨作廢發票」上線後，VOID 會有兩種來源、回填就不再單純——故此遷移
必須先於該功能落地。

欄位以 VARCHAR + CHECK 約束儲存（native_enum=False），故新增列舉值須重建 CHECK 約束。

Revision ID: f4c5d6e7a8b9
Revises: e3b4c5d6e7f8
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4c5d6e7a8b9"
down_revision: str | Sequence[str] | None = "e3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SALE_STATUS_CK = "salestatus"  # SQLAlchemy Enum(create_constraint=True) 以列舉名為約束名
_OLD_SALE_STATUSES = ("COMPLETED", "RETURNED")
_NEW_SALE_STATUSES = ("COMPLETED", "RETURNED", "VOIDED")
_VOID_REASON_CK = "invoicevoidreason"
_VOID_REASONS = ("SALE_VOID", "FULL_RETURN", "CORRECTION")


def _replace_check(table: str, name: str, values: tuple[str, ...], column: str) -> None:
    """重建列舉值的 CHECK 約束（VARCHAR + CHECK 儲存法）。

    先結清延遲的約束觸發事件：`sales` 上有 deferrable constraint trigger
    （trg_sales_tender_total），本 migration 的 downgrade 會先 UPDATE sales 再走到這裡，
    ALTER TABLE 會被 Postgres 以「has pending trigger events」拒絕。空庫不會踩到，
    有資料的庫（逐步 downgrade、或日後改成 transaction_per_migration）才會炸。
    """
    op.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    op.drop_constraint(name, table, type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(name, table, sa.text(f"{column} IN ({allowed})"))


def upgrade() -> None:
    """Upgrade schema."""
    # 1) sales.status 允許 VOIDED
    _replace_check("sales", _SALE_STATUS_CK, _NEW_SALE_STATUSES, "status")

    # 2) 回填：既有 invoice_status='VOID' 的銷售即「已作廢的銷售」（見檔頭說明，無歧義）。
    op.execute(
        sa.text(
            "UPDATE sales SET status = 'VOIDED' WHERE invoice_status = 'VOID'"
        )
    )
    # 3) 已作廢但**從未開過發票**的單，其 invoice_status 是被污染的 → 還原成 NOT_ISSUED。
    #    有發票者維持 VOID（那張發票確實作廢了），不動。
    op.execute(
        sa.text(
            "UPDATE sales SET invoice_status = 'NOT_ISSUED' "
            "WHERE invoice_status = 'VOID' "
            "AND NOT EXISTS (SELECT 1 FROM invoices WHERE invoices.sale_id = sales.id)"
        )
    )

    # 4) invoices.void_reason
    op.add_column("invoices", sa.Column("void_reason", sa.String(length=30), nullable=True))
    allowed = ", ".join(f"'{v}'" for v in _VOID_REASONS)
    op.create_check_constraint(
        _VOID_REASON_CK, "invoices", sa.text(f"void_reason IS NULL OR void_reason IN ({allowed})")
    )
    # 既有已作廢發票一律標為 SALE_VOID（此前唯一的作廢來源就是銷售作廢）。
    op.execute(
        sa.text(
            "UPDATE invoices SET void_reason = 'SALE_VOID' "
            "WHERE status IN ('VOID', 'VOID_PENDING')"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 還原：VOIDED 的銷售改回以 invoice_status 表達，再收回 CHECK 約束。
    # 註：回滾會失去「作廢原因」這個區分（FULL_RETURN 的發票在舊模型下看起來就像銷售作廢）。
    # 系統尚未上線、沒有需要保全的既有資料，故不在此設攔截；真要回滾就重建資料庫。
    op.execute(
        sa.text("UPDATE sales SET invoice_status = 'VOID' WHERE status = 'VOIDED'")
    )
    op.execute(sa.text("UPDATE sales SET status = 'COMPLETED' WHERE status = 'VOIDED'"))
    _replace_check("sales", _SALE_STATUS_CK, _OLD_SALE_STATUSES, "status")
    op.drop_constraint(_VOID_REASON_CK, "invoices", type_="check")
    op.drop_column("invoices", "void_reason")
