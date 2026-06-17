"""inventory 唯讀查詢 schema（T19-pre-B）。

金額以字串傳輸（§11）、新台幣整數元（§6）。序號品一般查詢**不含收購成本**
（成本屬敏感營業資訊，POS 查件不需要）；散裝堆含成本（docs/10 §5 /inventory
明列各堆顯示收購成本與售出進度）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
)

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class BrandRead(BaseModel):
    """品牌輸出（收購頁 combobox）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class BrandCreate(BaseModel):
    """品牌建立（查無即建；同名 get_or_create 冪等）。"""

    name: str = Field(min_length=1, max_length=100)


class ProductModelRead(BaseModel):
    """型號輸出（收購頁 combobox；選型號帶出其品牌）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    brand_id: int
    name: str


class ProductModelCreate(BaseModel):
    """型號建立（歸屬指定品牌；同品牌同名 get_or_create 冪等）。"""

    brand_id: int
    name: str = Field(min_length=1, max_length=150)


RateMultiple = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class CategoryRead(BaseModel):
    """分類輸出（收購頁 combobox；帶目標毛利率）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    target_margin_pct: int


class CategoryCreate(BaseModel):
    """分類建立（查無即建；未給 target 用店層級 default_margin_pct）。"""

    name: str = Field(min_length=1, max_length=100)
    target_margin_pct: int | None = Field(default=None, ge=0, le=99)


class CategoryTargetUpdate(BaseModel):
    """更新分類目標毛利率（manager）。"""

    target_margin_pct: int = Field(ge=0, le=99)


class PricingRuleRead(BaseModel):
    """分類×成色帶 定價規則輸出（收購定價輔助讀取）。"""

    model_config = ConfigDict(from_attributes=True)

    condition_band: Grade
    discount_ceiling_pct: int
    min_margin_pct: int
    min_price_multiple: RateMultiple


class PricingRuleUpdateItem(BaseModel):
    condition_band: Grade
    discount_ceiling_pct: int = Field(ge=0, le=99)
    min_margin_pct: int = Field(ge=0, le=99)
    min_price_multiple: Decimal = Field(gt=0)


class PricingRulesUpdate(BaseModel):
    """批次更新分類各成色帶規則（manager）。"""

    rules: list[PricingRuleUpdateItem]


class SerializedItemRead(BaseModel):
    """序號品輸出（POS 掃碼查件/庫存列表；不含 acquisition_cost）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    item_code: str
    name: str
    brand_id: int | None
    product_model_id: int | None
    category_id: int | None
    grade: Grade
    ownership_type: OwnershipType
    consignor_id: int | None
    commission_pct: int | None
    listed_price: NTDAmount
    status: SerializedItemStatus
    intake_date: datetime
    sold_date: datetime | None


class CatalogProductRead(BaseModel):
    """數量型商品輸出（POS 選件/庫存列表）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    sku: str
    name: str
    brand_id: int | None
    unit_price: NTDAmount
    quantity_on_hand: int
    reorder_point: int


class BulkLotRead(BaseModel):
    """散裝堆輸出（POS 明確選堆/庫存列表；含收購成本與售出進度）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    lot_code: str
    label: str | None
    name: str
    brand_id: int | None
    category_id: int | None
    grade: Grade
    acquisition_cost: NTDAmount
    acquisition_basis: BulkAcquisitionBasis
    unit_price: NTDAmount
    total_qty: int
    remaining_qty: int
    status: BulkLotStatus
