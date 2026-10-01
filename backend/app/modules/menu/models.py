"""menu 模型：餐飲/內用菜單（手沖咖啡等現做商品）。

與二手庫存（serialized/bulk）、一般商品（catalog）刻意分離：餐飲現做、不套門市活動折扣、
不可用購物金折抵、報表另列「餐飲營收」。

彈性菜單（docs/44 §3，2026-10-01 裁示推翻「扁平、不加價」）：
- `MenuCategory` 分類；品項以 `category_id` 指向。
- `MenuOptionGroup` 選項群組（溫度、豆種、加購…），以 min/max_select 表達必選／可選、單選／多選；
  一個群組可掛在多個品項上（`MenuItemOptionGroup`），改一次全部同步。
- `MenuOption` 群組內的選項，可加價（`price_delta` ≥ 0，含稅整數元）、可單獨停售。

刪除採**封存**（archived_at）而非實刪——歷史 sale_line 以 menu_item_id 外鍵指向，
實刪會破壞參照完整性。POS 只列「未封存且 is_available」者。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class MenuCategory(Base, TimestampMixin):
    __tablename__ = "menu_categories"
    __table_args__ = (
        Index(
            "uq_menu_categories_store_name_active",
            "store_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MenuItem(Base, TimestampMixin):
    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("stock_qty IS NULL OR stock_qty >= 0", name="ck_menu_items_stock_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    # 含稅整數元的**基本價**（與全系統金額慣例一致，§6）；選項加價另計。
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 成本（含耗材、包材等，由店主自行加總後填入；可空＝不知道）。裁示 2026-09-17。
    # **不做原料主檔與配方用量**：要讓系統自動算單品成本得建原料、單位換算與扣庫存，
    # 單店不划算。成交當下會快照到 `sale_lines.cost_snapshot`，日後調整不改寫歷史毛利。
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("menu_categories.id"), index=True)
    # 給客人看的介紹（風味、份量…）；線上點餐與 POS 共用。
    description: Mapped[str | None] = mapped_column(String(500))
    # POS 是否可點（上架/停售切換，不影響歷史）。
    is_available: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # 每日限量（docs/44 §3.7）：勾了就每天開店歸零，要填當天份數才能賣；沒勾＝不限量。
    # 「歸零」不靠排程：stock_qty 只在 stock_day＝今天（台北營業日）時有效，否則視為 0。
    daily_limited: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    stock_qty: Mapped[int | None] = mapped_column()
    stock_day: Mapped[date | None] = mapped_column(Date)
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # 封存（軟刪除）：非 NULL 即從 POS/管理清單隱藏，但保留供歷史 sale_line 參照。
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MenuOptionGroup(Base, TimestampMixin):
    __tablename__ = "menu_option_groups"
    __table_args__ = (
        CheckConstraint(
            "min_select >= 0 AND max_select >= 1 AND min_select <= max_select",
            name="ck_menu_option_groups_bounds",
        ),
        Index(
            "uq_menu_option_groups_store_name_active",
            "store_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    # 必選單選＝1/1；可選多選＝0/N。
    min_select: Mapped[int] = mapped_column()
    max_select: Mapped[int] = mapped_column()
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MenuOption(Base, TimestampMixin):
    __tablename__ = "menu_options"
    __table_args__ = (
        CheckConstraint("price_delta >= 0", name="ck_menu_options_price_delta_nonneg"),
        CheckConstraint("stock_qty IS NULL OR stock_qty >= 0", name="ck_menu_options_stock_nonneg"),
        Index(
            "uq_menu_options_group_name_active",
            "group_id",
            "name",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("menu_option_groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    # 加價（含稅整數元）；0＝不加價。
    price_delta: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 單一選項停售（例：某支豆子用完）。
    is_available: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # 每日限量（docs/44 §3.7）：勾了就每天開店歸零，要填當天份數才能賣；沒勾＝不限量。
    # 「歸零」不靠排程：stock_qty 只在 stock_day＝今天（台北營業日）時有效，否則視為 0。
    daily_limited: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    stock_qty: Mapped[int | None] = mapped_column()
    stock_day: Mapped[date | None] = mapped_column(Date)
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MenuItemOptionGroup(Base):
    """品項 ↔ 選項群組（多對多＋排序）。重掛時整批替換。"""

    __tablename__ = "menu_item_option_groups"

    item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("menu_option_groups.id"), primary_key=True, index=True
    )
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
