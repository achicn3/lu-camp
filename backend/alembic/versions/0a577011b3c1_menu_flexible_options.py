"""彈性菜單：分類、選項群組、選項、品項掛群組、品項介紹、銷售明細選項快照（docs/44 §3）。

既有 menu_items.category 字串轉成 menu_categories 列後移除該欄；既有品項沒有群組＝沒有選項，
照常可賣。降版時把分類名稱寫回字串欄；但選項群組、選項與品項介紹是店主手打、舊版沒地方放，
**有資料就拒絕降版**，不靜默丟掉（同 inventory_item_note 的做法）。

Revision ID: 0a577011b3c1
Revises: f8c4d2a91b63
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision = "0a577011b3c1"
down_revision = "f8c4d2a91b63"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "menu_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_menu_categories_store_id", "menu_categories", ["store_id"])
    op.create_index(
        "uq_menu_categories_store_name_active",
        "menu_categories",
        ["store_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "menu_option_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("min_select", sa.Integer(), nullable=False),
        sa.Column("max_select", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "min_select >= 0 AND max_select >= 1 AND min_select <= max_select",
            name="ck_menu_option_groups_bounds",
        ),
    )
    op.create_index("ix_menu_option_groups_store_id", "menu_option_groups", ["store_id"])
    op.create_index(
        "uq_menu_option_groups_store_name_active",
        "menu_option_groups",
        ["store_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "menu_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("menu_option_groups.id"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("price_delta", sa.Numeric(12, 0), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("price_delta >= 0", name="ck_menu_options_price_delta_nonneg"),
    )
    op.create_index("ix_menu_options_store_id", "menu_options", ["store_id"])
    op.create_index("ix_menu_options_group_id", "menu_options", ["group_id"])
    op.create_index(
        "uq_menu_options_group_name_active",
        "menu_options",
        ["group_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "menu_item_option_groups",
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("menu_items.id"), primary_key=True),
        sa.Column(
            "group_id", sa.Integer(), sa.ForeignKey("menu_option_groups.id"), primary_key=True
        ),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index("ix_menu_item_option_groups_group_id", "menu_item_option_groups", ["group_id"])
    op.create_index("ix_menu_item_option_groups_store_id", "menu_item_option_groups", ["store_id"])

    op.add_column(
        "menu_items",
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("menu_categories.id"), nullable=True),
    )
    op.create_index("ix_menu_items_category_id", "menu_items", ["category_id"])
    op.add_column("menu_items", sa.Column("description", sa.String(500), nullable=True))

    # 舊分類字串 → 分類列（同店同名一筆；空白視為未分類）。
    op.execute(
        """
        INSERT INTO menu_categories (store_id, name)
        SELECT DISTINCT store_id, btrim(category)
        FROM menu_items
        WHERE category IS NOT NULL AND btrim(category) <> ''
        """
    )
    op.execute(
        """
        UPDATE menu_items AS m
        SET category_id = c.id
        FROM menu_categories AS c
        WHERE c.store_id = m.store_id AND c.name = btrim(m.category)
        """
    )
    op.drop_column("menu_items", "category")

    # 結帳帶選項（O1b）：品名帶選項會變長、選項另存快照。
    op.alter_column(
        "sale_lines",
        "description",
        existing_type=sa.String(150),
        type_=sa.String(300),
        existing_nullable=False,
    )
    op.add_column(
        "sale_lines",
        sa.Column("menu_options_snapshot", postgresql.JSONB(), nullable=True),
    )


def abort_if_option_data_exists(conn: Connection) -> None:
    """降版會 DROP 這些表／欄；有店主建的資料就中止並指出哪裡有幾筆。"""
    checks = {
        "menu_option_groups": "SELECT count(*) FROM menu_option_groups",
        "menu_options": "SELECT count(*) FROM menu_options",
        "menu_items.description": "SELECT count(*) FROM menu_items WHERE description IS NOT NULL",
        "sale_lines.menu_options_snapshot": (
            "SELECT count(*) FROM sale_lines WHERE menu_options_snapshot IS NOT NULL"
        ),
        "sale_lines.description 超過 150 字": (
            "SELECT count(*) FROM sale_lines WHERE char_length(description) > 150"
        ),
    }
    found = []
    for label, sql in checks.items():
        n = conn.execute(sa.text(sql)).scalar_one()
        if n:
            found.append(f"{label} {n} 筆")
    if found:
        raise RuntimeError("拒絕降版：以下資料降版後無處保存——" + "、".join(found))


def downgrade() -> None:
    abort_if_option_data_exists(op.get_bind())
    op.drop_column("sale_lines", "menu_options_snapshot")
    op.alter_column(
        "sale_lines",
        "description",
        existing_type=sa.String(300),
        type_=sa.String(150),
        existing_nullable=False,
    )
    op.add_column("menu_items", sa.Column("category", sa.String(50), nullable=True))
    op.execute(
        """
        UPDATE menu_items AS m
        SET category = c.name
        FROM menu_categories AS c
        WHERE c.id = m.category_id
        """
    )
    op.drop_column("menu_items", "description")
    op.drop_index("ix_menu_items_category_id", table_name="menu_items")
    op.drop_column("menu_items", "category_id")
    op.drop_table("menu_item_option_groups")
    op.drop_table("menu_options")
    op.drop_table("menu_option_groups")
    op.drop_table("menu_categories")
