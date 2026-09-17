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

    # 先卸掉舊的全域唯一鍵：底下要替其他分店複製同版本號的列，唯一鍵還在就插不進去。
    op.drop_constraint("uq_agreement_versions_version", "agreement_versions", type_="unique")

    rows = conn.execute(sa.text("SELECT id FROM agreement_versions")).all()
    if rows:
        stores = [r[0] for r in conn.execute(sa.text("SELECT id FROM stores ORDER BY id")).all()]
        if not stores:
            raise RuntimeError("agreement_versions 有資料但 stores 是空的，無法決定歸屬店別")
        # 原本的版本列是**全店共用**的。全部塞給第一家店的話，其他店的既有簽署就會指到
        # 別人店的版本列，店別隔離破功。但已簽的任務**不能改指向**（DB 觸發器擋著：
        # 簽署內容與歸屬不可修改，那是法律證據），所以只能這樣拆：
        #   - 有簽署參照到共用列的那家店 → 原列歸它，簽署一動不動。
        #   - 其他店 → 各複製一份同版本號的列（內容一字不差），日後改版各走各的。
        #   - 若兩家以上都已有簽署參照 → 中止，交由人工決定，不擅自搬動任何人的證據。
        referencing = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT DISTINCT store_id FROM signature_tasks"
                    " WHERE agreement_version_id IS NOT NULL"
                )
            ).all()
        ]
        if len(referencing) > 1:
            raise RuntimeError(
                "有兩家以上分店的簽署共用同一份切結書版本列，且已簽署的歸屬不可修改；"
                "請人工決定各店版本歸屬後再升版"
            )
        owner = referencing[0] if referencing else stores[0]
        conn.execute(
            sa.text("UPDATE agreement_versions SET store_id = :sid WHERE store_id IS NULL"),
            {"sid": owner},
        )
        for store_id in stores:
            if store_id == owner:
                continue
            for (row_id,) in rows:
                conn.execute(
                    sa.text(
                        "INSERT INTO agreement_versions"
                        " (store_id, version, title, body, created_at)"
                        " SELECT :sid, version, title, body, created_at"
                        " FROM agreement_versions WHERE id = :rid"
                    ),
                    {"sid": store_id, "rid": row_id},
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
