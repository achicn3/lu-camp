"""sales.invoice_status 新增 PENDING_VOID（作廢已請求、平台尚未確認）

作廢一張已開立的發票要送 F0501 給平台，平台**可能拒絕**。在收到確認之前，那張發票在平台上
仍然有效——此時若把銷售的發票狀態直接顯示成「已作廢」，一旦 F0501 被拒，畫面就會永遠說謊，
而且沒有任何後續回呼會來改正它。

因此比照既有的 PENDING_ALLOWANCE（折讓開立中），新增 PENDING_VOID（作廢開立中）：
- 送出作廢請求 → PENDING_VOID
- F0501 核可 → VOID（確實作廢）
- F0401 失敗（那張發票從未在平台成立）→ NOT_ISSUED

列舉以 VARCHAR + CHECK 儲存，故須重建 CHECK 約束。

Revision ID: a3c5e7f9b1d2
Revises: f7a8b9c0d1e2
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c5e7f9b1d2"
down_revision: str | Sequence[str] | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "saleinvoicestatus"
_OLD = (
    "NOT_ISSUED",
    "PENDING_ISSUE",
    "ISSUED",
    "PENDING_ALLOWANCE",
    "ALLOWANCE",
    "VOID",
)
_NEW = (*_OLD, "PENDING_VOID")


def _replace_check(values: tuple[str, ...]) -> None:
    # 先把延遲的約束觸發事件結清（sales 上有 deferrable constraint trigger
    # trg_sales_tender_total）。同一個 `alembic upgrade head` 交易裡，前面的 migration 若
    # UPDATE 過 sales，這裡的 ALTER TABLE 會被 Postgres 以「has pending trigger events」拒絕
    # ——空庫不會踩到（沒有列就沒有事件），有資料的舊庫升級才會炸，例如新機部署或還原舊備份。
    op.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    op.drop_constraint(_CK, "sales", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(_CK, "sales", sa.text(f"invoice_status IN ({allowed})"))
    # 還原 deferred 模式：SET CONSTRAINTS 的效力及於整個交易，而 alembic 預設把整條鏈包在
    # 同一個交易裡——不還原的話，後續每一支 migration 都會靜默失去延遲約束的保護。
    # （全庫的 deferrable 約束皆為 INITIALLY DEFERRED，故 ALL DEFERRED 正好還原原狀。）
    op.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))


def upgrade() -> None:
    _replace_check(_NEW)


def downgrade() -> None:
    # 尚在作廢途中的銷售在舊模型下沒有對應狀態；退回「已開立」最貼近事實
    # （平台尚未確認作廢＝那張發票還有效）。
    op.execute(
        sa.text("UPDATE sales SET invoice_status = 'ISSUED' WHERE invoice_status = 'PENDING_VOID'")
    )
    _replace_check(_OLD)
