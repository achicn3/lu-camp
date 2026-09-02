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


# 兩段資料改寫提成常數：測試直接執行這裡的 SQL，而不是複製一份到測試檔——
# 複製品會讓測試全綠而真正的 migration 是錯的（實測：改了複製品，測試毫無反應）。
MERGE_ROLES_SQL = """
    UPDATE contacts
    SET roles = ARRAY(
        SELECT DISTINCT r FROM unnest(
            array_append(array_replace(roles, 'CONSIGNOR', 'SELLER'), 'MEMBER')
        ) AS r
        ORDER BY r
    )
"""

BACKFILL_SELLERS_SQL = """
    UPDATE contacts c
    SET roles = ARRAY(
        SELECT DISTINCT r FROM unnest(array_append(c.roles, 'SELLER')) AS r ORDER BY r
    )
    WHERE NOT ('SELLER' = ANY(c.roles))
      AND c.national_id_enc IS NOT NULL
      AND EXISTS (SELECT 1 FROM acquisitions a WHERE a.contact_id = c.id)
"""


def upgrade() -> None:
    # 一句話同時完成三件事，且順序不可分開跑：
    #   1. CONSIGNOR → SELLER
    #   2. 每個人補上 MEMBER
    #   3. 去重（原本 SELLER+CONSIGNOR 的人換完會有兩個 SELLER）
    # 用 ARRAY(SELECT DISTINCT …) 而非 array_agg：後者對空陣列回 NULL，
    # 會把「沒有任何角色」的那 2 筆（實測資料存在）從 {} 變成 NULL 而違反 NOT NULL。
    op.execute(MERGE_ROLES_SQL)
    # 回填歷史賣方：舊流程只要求「有身分證字號」就能完成收購，不強制帶 SELLER 角色，
    # 所以有人賣過東西卻沒被標記（實測 877 位賣方中有 1 位）。不補的話「SELLER」這個
    # 標記名不副實——它宣稱的意思是「賣過東西給店裡」。
    #
    # **證號已清除者不回填**（fail closed）：曾收購但證號後來被清掉的人若補上 SELLER，
    # 會造出「賣方卻無身分證字號」——那正是防贓物登記要求的不變量所禁止的。
    # 寧可少標一個人（他的收購紀錄仍在，查得到），也不要造出違反不變量的資料。
    op.execute(BACKFILL_SELLERS_SQL)


def downgrade() -> None:
    # 不可逆：合併後無從得知某人原本是「賣方」還是「寄售人」，補上的 MEMBER 也無從
    # 分辨是本來就有還是這次加的。硬回填只會產生看似正確、實則捏造的歷史。
    raise NotImplementedError(
        "contacts 身分合併不可逆：CONSIGNOR 與 SELLER 合併後無從還原原始分類。"
        "若需回退請自備該次 upgrade 前的備份。"
    )
