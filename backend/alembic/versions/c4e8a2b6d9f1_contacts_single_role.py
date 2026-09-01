"""contacts 身分簡化：CONSIGNOR 併入 SELLER，每個人補上 MEMBER

店主裁示（2026-09-01）：不再分「賣方」與「寄售人」——兩者在程式裡的待遇完全一樣
（都必須有身分證字號），分開只是多一個要店員判斷的欄位。商品是買斷來的還是寄售的，
是**商品的屬性**（inventory 的 source_kind），不是人的屬性。同時每個人建檔就是會員。

Revision ID: c4e8a2b6d9f1
Revises: a8c1f4e7b2d5
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4e8a2b6d9f1"
down_revision: str | Sequence[str] | None = "a8c1f4e7b2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 一句話同時完成三件事，且順序不可分開跑：
    #   1. CONSIGNOR → SELLER
    #   2. 每個人補上 MEMBER
    #   3. 去重（原本 SELLER+CONSIGNOR 的人換完會有兩個 SELLER）
    # 用 array(select distinct …) 而非 array_agg：後者對空陣列回 NULL，
    # 會把「沒有任何角色」的那 2 筆（實測資料存在）從 {} 變成 NULL 而違反 NOT NULL。
    op.execute(
        """
        UPDATE contacts
        SET roles = ARRAY(
            SELECT DISTINCT r FROM unnest(
                array_append(
                    array_replace(roles, 'CONSIGNOR', 'SELLER'),
                    'MEMBER'
                )
            ) AS r
            ORDER BY r
        )
        """
    )


def downgrade() -> None:
    # 不可逆：合併後無從得知某人原本是「賣方」還是「寄售人」，補上的 MEMBER 也無從
    # 分辨是本來就有還是這次加的。硬回填只會產生看似正確、實則捏造的歷史。
    raise NotImplementedError(
        "contacts 身分合併不可逆：CONSIGNOR 與 SELLER 合併後無從還原原始分類。"
        "若需回退請自備該次 upgrade 前的備份。"
    )
