"""退貨發票處置同意可由非會員簽署：signature_tasks.contact_id 放寬為可空

零售退貨的買受人多半是臨櫃客人，店內沒有他的會員檔。若 `contact_id` 維持 NOT NULL，
「折讓／作廢一律須買受人同意」這條規則會讓**所有匿名交易的已開發票退貨全部無法完成**。

因此把欄位放寬為可空，並以 CHECK 精確限縮：**只有 RETURN_INVOICE_CONSENT 可以無會員**，
其餘類型（收購切結、購物金扣抵、交易簽收）仍強制綁定對象，既有保證不被稀釋。

複合外鍵 `fk_signature_tasks_contact_store` 為 MATCH SIMPLE，任一欄為 NULL 即不檢查，
不需改動。

Revision ID: a7b8c9d0e1f2
Revises: f4c5d6e7a8b9
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f4c5d6e7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "ck_signature_tasks_contact_required"
# 列舉以 VARCHAR + CHECK 儲存（native_enum=False），新增列舉值必須重建該 CHECK，
# 否則只有由 models 直接建表的測試庫能寫入，實際遷移過的資料庫會被擋（本次即由真 DB 煙霧抓出）。
_KIND_CK = "signaturetaskkind"
_OLD_KINDS = ("ACQUISITION_AFFIDAVIT", "STORE_CREDIT_USE", "TRANSACTION_ACK")
_NEW_KINDS = (*_OLD_KINDS, "RETURN_INVOICE_CONSENT")


def _replace_kind_check(values: tuple[str, ...]) -> None:
    op.drop_constraint(_KIND_CK, "signature_tasks", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(_KIND_CK, "signature_tasks", sa.text(f"kind IN ({allowed})"))


def upgrade() -> None:
    _replace_kind_check(_NEW_KINDS)
    op.alter_column(
        "signature_tasks",
        "contact_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_check_constraint(
        _CK,
        "signature_tasks",
        "contact_id IS NOT NULL OR kind = 'RETURN_INVOICE_CONSENT'",
    )


def downgrade() -> None:
    op.drop_constraint(_CK, "signature_tasks", type_="check")
    # 註：若庫內已有無會員的退貨同意任務，回滾會因 NOT NULL 而失敗。系統尚未上線、
    # 沒有需要保全的既有資料，故不另設攔截；真要回滾就重建資料庫。
    op.alter_column(
        "signature_tasks",
        "contact_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    _replace_kind_check(_OLD_KINDS)
