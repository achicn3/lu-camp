"""inventory 模型：品牌/型號主檔、數量型商品、序號單品、散裝批、庫存異動帳。

每張表帶 store_id（多分店就緒）。金額用 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
列舉以 native_enum=False + CHECK 儲存（VARCHAR），避免 PG ENUM 型別在 downgrade 殘留。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    ItemKind,
    OwnershipType,
    SerializedItemStatus,
    StockDirection,
    StockReason,
)


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Brand(Base, TimestampMixin):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("store_id", "name", name="uq_brands_store_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class ProductModel(Base, TimestampMixin):
    __tablename__ = "product_models"
    # 型號以 (store, brand, name) 唯一：不同品牌可有同名型號（F6 品牌範圍 autocomplete）。
    __table_args__ = (
        UniqueConstraint("store_id", "brand_id", "name", name="uq_product_models_store_brand_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))


class Category(Base, TimestampMixin):
    """分類（定價骨幹；docs/10 §/acquisition）：每店每名唯一，帶目標毛利率。"""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("store_id", "name", name="uq_categories_store_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    target_margin_pct: Mapped[int] = mapped_column()  # 目標毛利率（整數百分數）


class CategoryPricingRule(Base, TimestampMixin):
    """分類 × 成色帶 的收購定價規則（雙重約束參數；F6 收購定價輔助讀取）。

    成色帶限 S/A/B/C/D（E 走散裝，無此規則）。manager 可批次更新。
    """

    __tablename__ = "category_pricing_rules"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "category_id", "condition_band", name="uq_category_pricing_rule_band"
        ),
        CheckConstraint("condition_band <> 'E'", name="ck_pricing_rule_band_not_e"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    condition_band: Mapped[Grade] = mapped_column(_enum_col(Grade))
    discount_ceiling_pct: Mapped[int] = mapped_column()  # 最高折讓（離轉售價的折扣上限，整數%）
    min_margin_pct: Mapped[int] = mapped_column()  # 最低毛利率（整數百分數）
    min_price_multiple: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # 轉售價 ÷ 成本 的最低倍數


class CatalogProduct(Base, TimestampMixin):
    __tablename__ = "catalog_products"
    # 同店 SKU 唯一（與 brands/suppliers 同慣例）：DB 後盾，防 app 層 check-then-insert 競態。
    __table_args__ = (UniqueConstraint("store_id", "sku", name="uq_catalog_products_store_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(150))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    quantity_on_hand: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    reorder_point: Mapped[int] = mapped_column(default=0, server_default=text("0"))


class SerializedItem(Base, TimestampMixin):
    """序號單品（S-D）。item_code 建檔即固定、全域唯一（與 POS 掃碼同一套碼）。"""

    __tablename__ = "serialized_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    item_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    product_model_id: Mapped[int | None] = mapped_column(ForeignKey("product_models.id"))
    grade: Mapped[Grade] = mapped_column(_enum_col(Grade))
    ownership_type: Mapped[OwnershipType] = mapped_column(_enum_col(OwnershipType))
    acquisition_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    consignor_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    commission_pct: Mapped[int | None] = mapped_column()
    listed_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    status: Mapped[SerializedItemStatus] = mapped_column(
        _enum_col(SerializedItemStatus),
        default=SerializedItemStatus.IN_STOCK,
        server_default=SerializedItemStatus.IN_STOCK.value,
    )
    acquisition_id: Mapped[int | None] = mapped_column(ForeignKey("acquisitions.id"))
    # 分類（F6 additive 持久化；先 nullable，日後 backfill 後收緊為 NOT NULL）。
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT")
    )
    intake_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sold_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BulkLot(Base, TimestampMixin):
    """散裝批（E 級）。lot_code 建檔即固定、全域唯一。每件成本 = acquisition_cost/total_qty。"""

    __tablename__ = "bulk_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    lot_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(150))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    grade: Mapped[Grade] = mapped_column(_enum_col(Grade))
    consignor_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    acquisition_cost: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    acquisition_basis: Mapped[BulkAcquisitionBasis] = mapped_column(_enum_col(BulkAcquisitionBasis))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    total_qty: Mapped[int] = mapped_column()
    remaining_qty: Mapped[int] = mapped_column()
    status: Mapped[BulkLotStatus] = mapped_column(
        _enum_col(BulkLotStatus),
        default=BulkLotStatus.ON_SALE,
        server_default=BulkLotStatus.ON_SALE.value,
    )
    acquisition_id: Mapped[int | None] = mapped_column(ForeignKey("acquisitions.id"))
    # 分類（F6 additive 持久化；散裝選填、恆 nullable）。
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT")
    )
    intake_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StockMovement(Base):
    """庫存異動帳（append-only 帳；無 updated_at）。"""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    item_kind: Mapped[ItemKind] = mapped_column(_enum_col(ItemKind))
    serialized_item_id: Mapped[int | None] = mapped_column(ForeignKey("serialized_items.id"))
    catalog_product_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_products.id"))
    bulk_lot_id: Mapped[int | None] = mapped_column(ForeignKey("bulk_lots.id"))
    direction: Mapped[StockDirection] = mapped_column(_enum_col(StockDirection))
    qty: Mapped[int] = mapped_column()
    reason: Mapped[StockReason] = mapped_column(_enum_col(StockReason))
    ref_type: Mapped[str | None] = mapped_column(String(50))
    ref_id: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
