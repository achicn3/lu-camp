"""手沖體驗卡與加購角色（docs/63 §4、§6；M1c；店主 2026-10-08）。

- 新表 online_menu_experiences：既有品項＋預選選項的呈現（標題、風味、包含內容、
  配色、插畫、抽卡動畫），不存價格／成本／庫存。原品項真刪時 cascade。
- online_menu_presentations 加 role（加購角色；NULL＝不參與加購）。

Revision ID: a6c2e8f4d1b7
Revises: e3a9c5d1f7b2
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a6c2e8f4d1b7"
down_revision = "e3a9c5d1f7b2"
branch_labels = None
depends_on = None


def _choice(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=20, create_constraint=True)


def upgrade() -> None:
    op.add_column(
        "online_menu_presentations",
        sa.Column(
            "role",
            _choice("menuupsellrole", "coffee", "dessert", "experience", "bean", "drip", "other"),
            nullable=True,
        ),
    )
    op.create_table(
        "online_menu_experiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "menu_item_id",
            sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("option_ids", JSONB(), nullable=False),
        sa.Column("title", sa.String(30), nullable=False),
        sa.Column("tag", sa.String(12)),
        sa.Column("origin", sa.String(60)),
        sa.Column("notes", sa.String(80)),
        sa.Column("description", sa.String(300)),
        sa.Column("includes", JSONB(), nullable=False),
        sa.Column(
            "theme",
            _choice("brewcardtheme", "peach", "honey", "citrus", "wine", "forest", "ink"),
            nullable=False,
        ),
        sa.Column(
            "art",
            _choice("brewcardart", "peach", "vanilla", "citrus", "rum", "none"),
            nullable=False,
        ),
        sa.Column(
            "effect",
            _choice(
                "brewdraweffect", "random", "soar", "truck", "smash", "seal", "shuffle", "bloom"
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("sort_order BETWEEN 0 AND 9999", name="ck_online_menu_experiences_sort"),
    )
    op.create_index("ix_online_menu_experiences_store_id", "online_menu_experiences", ["store_id"])
    op.create_index(
        "ix_online_menu_experiences_menu_item_id", "online_menu_experiences", ["menu_item_id"]
    )


def downgrade() -> None:
    # 體驗卡只是呈現設定（價格、庫存、銷售都在原品項），降版刪掉不影響任何金額。
    op.drop_index("ix_online_menu_experiences_menu_item_id", table_name="online_menu_experiences")
    op.drop_index("ix_online_menu_experiences_store_id", table_name="online_menu_experiences")
    op.drop_table("online_menu_experiences")
    op.drop_column("online_menu_presentations", "role")
    op.execute("ALTER TABLE online_menu_presentations DROP CONSTRAINT IF EXISTS menuupsellrole")
