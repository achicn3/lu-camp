"""menu 模型：餐飲/內用菜單（手沖咖啡等現做商品）。

與二手庫存（serialized/bulk）、一般商品（catalog）刻意分離：餐飲現做、不套門市活動折扣、
報表另列「餐飲營收」（2026-10-09 起可用購物金折抵）。

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
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
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


class MenuPhoto(Base, TimestampMixin):
    """菜單照片（docs/44 §3.4）：已轉成 WebP、去掉 EXIF 的成品，以內容雜湊去重。

    存在資料庫（店主 2026-10-02 裁示）：每晚備份到 R2 與還原演練自動涵蓋，換機不掉照片。
    只增不刪——品項換照片後，舊照片可能仍被已發佈的線上菜單快照引用。
    """

    __tablename__ = "menu_photos"
    __table_args__ = (
        UniqueConstraint("store_id", "sha256", name="uq_menu_photos_store_sha256"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_menu_photos_sha256_hex"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    width: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column()


class MenuItem(Base, TimestampMixin):
    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("stock_qty IS NULL OR stock_qty >= 0", name="ck_menu_items_stock_nonneg"),
        # 照片要是同一家店的（複合外鍵，不能指到別店的照片）。
        ForeignKeyConstraint(
            ["store_id", "photo_sha256"],
            ["menu_photos.store_id", "menu_photos.sha256"],
            name="fk_menu_items_photo",
        ),
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
    # 照片（docs/44 §3.4）：`menu_photos` 的內容雜湊；沒照片＝null。
    photo_sha256: Mapped[str | None] = mapped_column(String(64))
    # POS 是否可點（上架/停售切換，不影響歷史）。
    is_available: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # 每日限量（docs/44 §3.7）：勾了就每天開店歸零，要填當天份數才能賣；沒勾＝不限量。
    # 「歸零」不靠排程：stock_qty 只在 stock_day＝今天（台北營業日）時有效，否則視為 0。
    daily_limited: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    stock_qty: Mapped[int | None] = mapped_column()
    stock_day: Mapped[date | None] = mapped_column(Date)
    # 份數版本：每次「直接設定份數」或切換每日限量就 +1。結帳把扣到的版本記在明細上，
    # 作廢只在版本沒變時加回——重設代表店員實際數過、數字已反映現況，再加回會多算
    # （Codex 對抗審查 O1c）。用版本號而不是時間比先後，不受交易邊界影響。
    stock_generation: Mapped[int] = mapped_column(default=0, server_default=text("0"))
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
    # 選項成本（docs/49 F1，例：燕麥奶多 8 元）；可空＝沒有額外材料，結帳時視為 0。
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 單一選項停售（例：某支豆子用完）。
    is_available: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    # 每日限量（docs/44 §3.7）：勾了就每天開店歸零，要填當天份數才能賣；沒勾＝不限量。
    # 「歸零」不靠排程：stock_qty 只在 stock_day＝今天（台北營業日）時有效，否則視為 0。
    daily_limited: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    stock_qty: Mapped[int | None] = mapped_column()
    stock_day: Mapped[date | None] = mapped_column(Date)
    # 份數版本：每次「直接設定份數」或切換每日限量就 +1。結帳把扣到的版本記在明細上，
    # 作廢只在版本沒變時加回——重設代表店員實際數過、數字已反映現況，再加回會多算
    # （Codex 對抗審查 O1c）。用版本號而不是時間比先後，不受交易邊界影響。
    stock_generation: Mapped[int] = mapped_column(default=0, server_default=text("0"))
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


class MenuStockAdjustment(Base):
    """每日份數的加減紀錄（補貨／報廢／盤點校正）；報廢統計的來源。只增不改。"""

    __tablename__ = "menu_stock_adjustments"
    __table_args__ = (
        CheckConstraint(
            "(reason = 'RESTOCK' AND delta > 0) "
            "OR (reason IN ('WASTE','CORRECTION') AND delta < 0)",
            name="ck_menu_stock_adjustments_reason_sign",
        ),
        CheckConstraint("target_kind IN ('item','option')", name="ck_menu_stock_adjustments_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(10))
    target_id: Mapped[int] = mapped_column()
    delta: Mapped[int] = mapped_column()
    reason: Mapped[str] = mapped_column(String(20))
    business_date: Mapped[date] = mapped_column(Date, index=True)
    # 按下當下一份的成本（docs/49 §3）；之後改成本不改寫損耗。成本未知＝NULL（只計份數）。
    unit_cost_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
