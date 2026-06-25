"""inventory 唯讀查詢 schema（T19-pre-B）。

金額以字串傳輸（§11）、新台幣整數元（§6）。序號品一般查詢**不含收購成本**
（成本屬敏感營業資訊，POS 查件不需要）；散裝堆含成本（docs/10 §5 /inventory
明列各堆顯示收購成本與售出進度）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

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


class ItemSourceRead(BaseModel):
    """庫存明細「來源」：買斷賣方或寄售人（不含 national_id）。"""

    contact_id: int | None
    name: str | None
    phone: str | None
    kind: str  # "SELLER"（買斷賣方）/ "CONSIGNOR"（寄售人）


class ItemHistoryEvent(BaseModel):
    """庫存明細歷史事件（一筆庫存異動帳對應一列）。"""

    at: datetime
    event: str  # 入庫（收購）/ 售出 / 退貨入庫 / 寄售退回 / 作廢出庫…
    qty: int
    note: str | None = None


class SerializedItemDetailRead(BaseModel):
    """序號品明細（庫存逐件「詳細」）：含成本/售價/來源/收購/售出/完整異動歷史。"""

    id: int
    item_code: str
    name: str
    brand_id: int | None
    category_id: int | None
    grade: Grade
    ownership_type: OwnershipType
    status: SerializedItemStatus
    commission_pct: int | None
    listed_price: NTDAmount
    acquisition_cost: NTDAmount | None
    intake_date: datetime
    sold_date: datetime | None
    sold_price: NTDAmount | None  # 實際成交（折後）價
    margin: NTDAmount | None  # 買斷已售：成交價 − 收購成本
    source: ItemSourceRead | None
    acquisition_id: int | None
    acquisition_type: str | None
    sale_id: int | None
    history: list[ItemHistoryEvent]


class CatalogProductCreateRequest(BaseModel):
    """新增數量型商品（上架）：廠商採購商品先建檔，之後才能建採購單→收貨補庫存。

    初始庫存固定 0（補庫存一律走採購收貨，留痕）；reorder_point 為低庫存提醒點。
    """

    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=150)
    unit_price: NTDAmount
    reorder_point: int = Field(default=0, ge=0)
    brand_id: int | None = None

    @field_validator("sku", "name")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不可為空白")
        return cleaned

    @field_validator("unit_price")
    @classmethod
    def _positive_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("售價必須為整數元")
        if value <= 0:
            raise ValueError("售價必須為正")
        return value


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
