"""contacts 同店 phone 唯一約束（手機為店內聯絡人唯一識別）

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-24

手機號碼於 API 層必填、同店唯一，供以手機精確查找既有會員、避免重複建檔。
phone 為 NULL 時多筆不衝突（Postgres unique 視 NULL 相異），不影響不帶手機的內部資料。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_contacts_store_phone", "contacts", ["store_id", "phone"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_contacts_store_phone", "contacts", type_="unique")
