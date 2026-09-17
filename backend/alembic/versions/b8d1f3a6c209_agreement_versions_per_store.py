"""agreement_versions 加 store_id／created_by_user_id，版本號改以店為單位唯一

2026-09-17 需求：切結書全文要能在設定頁隨時修改。版本列不可變（docs/23 §5），
所以「修改」＝為該店新增一列、版本號 +1，舊列一字不動——已簽的簽名永遠指向
簽署當下那一份全文。

原表沒有 `store_id`，違反 CLAUDE.md §4（每張業務表都要有 store_id），而且一旦開放
店家自行改內文，A 店改版會害 B 店版本號跳號。故：
- 加 `store_id`（FK stores）；既有列回填到**最早建立的那家店**（目前單店）。
- 唯一鍵由 `(version)` 改為 `(store_id, version)`。
- 加 `created_by_user_id`（可空；內建版本落庫時沒有改版者）。

回填策略：表中若已有列卻連一家店都沒有，代表資料不一致，直接中止而不亂猜。

Revision ID: b8d1f3a6c209
Revises: a4e7c2b9f6d1
Create Date: 2026-09-17 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d1f3a6c209"
down_revision: str | Sequence[str] | None = "a4e7c2b9f6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    op.add_column("agreement_versions", sa.Column("store_id", sa.Integer(), nullable=True))
    op.add_column(
        "agreement_versions", sa.Column("created_by_user_id", sa.Integer(), nullable=True)
    )

    rows = conn.execute(sa.text("SELECT count(*) FROM agreement_versions")).scalar_one()
    if rows:
        store_id = conn.execute(sa.text("SELECT min(id) FROM stores")).scalar()
        if store_id is None:
            raise RuntimeError("agreement_versions 有資料但 stores 是空的，無法決定歸屬店別")
        conn.execute(
            sa.text("UPDATE agreement_versions SET store_id = :sid WHERE store_id IS NULL"),
            {"sid": store_id},
        )

    op.alter_column("agreement_versions", "store_id", nullable=False)
    op.create_index(
        "ix_agreement_versions_store_id", "agreement_versions", ["store_id"], unique=False
    )
    op.create_foreign_key(
        "fk_agreement_versions_store_id_stores",
        "agreement_versions",
        "stores",
        ["store_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_agreement_versions_created_by_user_id_users",
        "agreement_versions",
        "users",
        ["created_by_user_id"],
        ["id"],
    )
    op.drop_constraint("uq_agreement_versions_version", "agreement_versions", type_="unique")
    op.create_unique_constraint(
        "uq_agreement_versions_store_version", "agreement_versions", ["store_id", "version"]
    )


def downgrade() -> None:
    """Downgrade schema.

    版本號要變回全域唯一，所以只有「單店」時降得回去；多店資料降版會撞唯一鍵，
    與其丟掉其他分店的切結書，不如中止。
    """
    conn = op.get_bind()
    stores = conn.execute(
        sa.text("SELECT count(DISTINCT store_id) FROM agreement_versions")
    ).scalar_one()
    if stores > 1:
        raise RuntimeError("已有多家店的切結書版本，降版會使版本號衝突，請先人工處理")
    op.drop_constraint("uq_agreement_versions_store_version", "agreement_versions", type_="unique")
    op.create_unique_constraint(
        "uq_agreement_versions_version", "agreement_versions", ["version"]
    )
    op.drop_constraint(
        "fk_agreement_versions_created_by_user_id_users", "agreement_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_agreement_versions_store_id_stores", "agreement_versions", type_="foreignkey"
    )
    op.drop_index("ix_agreement_versions_store_id", table_name="agreement_versions")
    op.drop_column("agreement_versions", "created_by_user_id")
    op.drop_column("agreement_versions", "store_id")
