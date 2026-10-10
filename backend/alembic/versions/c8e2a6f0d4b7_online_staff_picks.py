"""店員推薦清單取代逐項「露坑推薦」（店主 2026-10-10）。

- 新表 online_staff_picks：每店一份有序清單（餐飲品項／手沖體驗卡／帶著走商品）。
- 搬資料：原本勾了「露坑推薦」的餐飲品項，照菜單排序放進清單；然後移除
  online_menu_presentations.is_recommended。

降版：欄位加回、清單裡的餐飲品項標回推薦（體驗卡與帶著走商品舊版放不下，會遺失）。

Revision ID: c8e2a6f0d4b7
Revises: b6d0f4a8c2e5
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c8e2a6f0d4b7"
down_revision = "b6d0f4a8c2e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "online_staff_picks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("items", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", name="online_staff_picks_store_id_key"),
    )
    # 清單上限 30 項（API 與雲端都照這個收）：超過的照菜單排序只搬前 30 個（Codex 第一輪）。
    op.execute(
        """
        INSERT INTO online_staff_picks (store_id, items)
        SELECT store_id,
               jsonb_agg(jsonb_build_object('kind', 'item', 'id', menu_item_id) ORDER BY position)
        FROM (
            SELECT p.store_id, p.menu_item_id,
                   row_number() OVER (
                       PARTITION BY p.store_id ORDER BY m.sort_order, m.id
                   ) AS position
            FROM online_menu_presentations p
            JOIN menu_items m ON m.id = p.menu_item_id
            WHERE p.is_recommended AND m.archived_at IS NULL
        ) ranked
        WHERE position <= 30
        GROUP BY store_id
        """
    )
    op.drop_column("online_menu_presentations", "is_recommended")


def downgrade() -> None:
    op.add_column(
        "online_menu_presentations",
        sa.Column("is_recommended", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # 清單裡的餐飲品項標回推薦；從沒存過線上呈現的品項補一列（其餘設定用預設值；Codex 第二輪）。
    op.execute(
        """
        INSERT INTO online_menu_presentations (store_id, menu_item_id, is_recommended)
        SELECT k.store_id, m.id, true
        FROM online_staff_picks k
        CROSS JOIN LATERAL jsonb_array_elements(k.items) e
        JOIN menu_items m ON m.id = (e->>'id')::int AND m.store_id = k.store_id
        WHERE e->>'kind' = 'item'
        ON CONFLICT (store_id, menu_item_id) DO UPDATE SET is_recommended = true
        """
    )
    op.drop_table("online_staff_picks")
