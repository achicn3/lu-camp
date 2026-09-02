"""inventory 唯讀查詢 schema（T19-pre-B）。

金額以字串傳輸（§11）、新台幣整數元（§6）。序號品一般查詢**不含收購成本**
（成本屬敏感營業資訊，POS 查件不需要）；散裝堆含成本（docs/10 §5 /inventory
明列各堆顯示收購成本與售出進度）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.core.money import ensure_ntd_fits_numeric_12, format_ntd, format_rate
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
)

NTDAmount = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]
NTDAmountOpt = Annotated[
    Decimal | None, PlainSerializer(
        lambda d: None if d is None else format_ntd(d), return_type=str | None
    )
]


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


RateMultiple = Annotated[Decimal, PlainSerializer(format_rate, return_type=str)]


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


class PriceUpdateRequest(BaseModel):
    """改售價（manager；含稅整數元 > 0；序號品=標價、一般商品/散裝=單價）。"""

    unit_price: NTDAmount

    @field_validator("unit_price")
    @classmethod
    def _positive_integer(cls, v: Decimal) -> Decimal:
        if v <= 0 or v != v.to_integral_value():
            raise ValueError("售價須為正整數元")
        ensure_ntd_fits_numeric_12(v, field="售價")
        return v


class NoteUpdateRequest(BaseModel):
    """改商品備註（一般店員即可；不涉金額，故不比照改價限管理者、也不限在庫）。

    單一自由欄位，兼「商品狀況說明」與「內部作業備忘」（2026-09-02 裁示）。
    空字串/全空白一律存 NULL——否則 POS 結帳會為了空白備註跳一個沒有內容的提醒。
    """

    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned or None


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
    note: str | None = None


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
    note: str | None = None
    history: list[ItemHistoryEvent]


class CatalogPurchaseRead(BaseModel):
    """一般商品的一筆進貨（供應商/訂購量/已收量/進貨單價/狀態/時間）。"""

    po_id: int
    supplier_id: int
    supplier_name: str
    qty: int
    received_qty: int
    unit_cost: NTDAmount
    status: str
    ordered_at: datetime
    received_at: datetime | None


class CatalogProductDetailRead(BaseModel):
    """一般商品明細（庫存逐件「詳細」）：售價/現量＋經銷商進貨歷史＋完整異動歷史。"""

    id: int
    sku: str
    name: str
    brand_id: int | None
    unit_price: NTDAmount
    # 成本＝最近一次收貨的進價（裁示 2026-08-03：採購收貨自動帶入）。從未收貨過則為 null，
    # 報表沿用「成本未知不假造毛利」的口徑。已成交的明細存有成本快照，不受日後變動影響。
    unit_cost: NTDAmountOpt = None
    quantity_on_hand: int
    reorder_point: int
    note: str | None = None
    purchases: list[CatalogPurchaseRead]
    history: list[ItemHistoryEvent]


class BulkLotDetailRead(BaseModel):
    """散裝批明細（庫存逐件「詳細」）：來源/收購成本/均一價/剩餘＋入庫時間＋異動歷史。"""

    id: int
    lot_code: str
    name: str
    brand_id: int | None
    category_id: int | None
    grade: Grade
    acquisition_cost: NTDAmount
    unit_price: NTDAmount
    total_qty: int
    remaining_qty: int
    intake_date: datetime
    source: ItemSourceRead | None
    acquisition_id: int | None
    acquisition_type: str | None
    note: str | None = None
    history: list[ItemHistoryEvent]


class CatalogProductCreateRequest(BaseModel):
    """新增一般商品（上架）：廠商採購商品先建檔，之後才能建採購單→收貨補庫存。

    SKU 可留白由系統產生；初始庫存固定 0（補庫存一律走採購收貨，留痕）；
    reorder_point 為低庫存提醒點。
    """

    sku: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=150)
    unit_price: NTDAmount
    reorder_point: int = Field(default=0, ge=0)
    brand_id: int | None = None
    # 商品備註（選填）：採購品沒有收購單，建檔是唯一能在源頭寫下備註的地方。
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _blank_note_to_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @field_validator("sku", mode="before")
    @classmethod
    def _strip_optional_sku(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = value.strip()
        return cleaned or None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
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
        ensure_ntd_fits_numeric_12(value, field="售價")
        return value


class CatalogProductRead(BaseModel):
    """一般商品輸出（POS 選件/庫存列表）。

    incoming_qty＝在途待到貨量（未收完的採購單累計待收：Σ(訂購−已收)，狀態 ORDERED/PARTIAL）；
    供低庫存提醒判斷是否已有補貨在路上、避免重複採購。清單以外的情境預設 0。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    sku: str
    name: str
    brand_id: int | None
    unit_price: NTDAmount
    quantity_on_hand: int
    reorder_point: int
    note: str | None = None
    incoming_qty: int = 0


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
    note: str | None = None
