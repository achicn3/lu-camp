"""campaigns API schema（docs/21）。折扣 1-99；金額不在此（活動只存折扣率）。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, Field, PlainSerializer, model_validator

from app.core.money import format_ntd
from app.core.time import AwareDateTime
from app.shared.enums import (
    BundlePackItemType,
    CampaignKind,
    CampaignStatus,
    CampaignTargetMode,
    CampaignTargetType,
)

# 含稅整數元（特價、折金額）：輸入必須 > 0、無小數；輸出為字串（§6 前端傳輸一律字串）。
NTDPositive = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=0)]
NTDAmountOpt = Annotated[
    Decimal | None,
    PlainSerializer(lambda d: None if d is None else format_ntd(d), return_type=str | None),
]

# 買 N 送 M 的件數（N、M 各 1–99）。
PROMO_QTY_MIN = 1
PROMO_QTY_MAX = 99
PromoQty = Annotated[int, Field(ge=PROMO_QTY_MIN, le=PROMO_QTY_MAX)]

# 一個活動最多掛幾條範圍條件（包含＋排除）；再多就該用分類或品牌。
CAMPAIGN_TARGETS_MAX = 200


class CampaignTargetInput(BaseModel):
    """一條範圍條件：包含或排除某個分類／品牌／型號／單件／一般商品／販售籃（須屬本店）。"""

    mode: CampaignTargetMode
    target_type: CampaignTargetType
    target_id: Annotated[int, Field(gt=0)]


class CampaignTargetRead(CampaignTargetInput):
    label: str
    """顯示用名稱（型號含品牌、單件含條碼）。"""


# 組合價的格子數（docs/40 §4）：店主 2026-10-10 起一樣也可以（例：同款買 3 件一個價）。
BUNDLE_SLOTS_MIN = 1
BUNDLE_SLOTS_MAX = 10


class BundleSlotTargetInput(BaseModel):
    """格子的一條範圍（只有包含；須屬本店）。"""

    target_type: CampaignTargetType
    target_id: Annotated[int, Field(gt=0)]


class BundleSlotTargetRead(BundleSlotTargetInput):
    label: str


class BundleSlotInput(BaseModel):
    """組合包的一個格子：符合任一範圍的商品要湊 qty 件。"""

    qty: PromoQty
    targets: Annotated[
        list[BundleSlotTargetInput], Field(min_length=1, max_length=CAMPAIGN_TARGETS_MAX)
    ]


class BundleSlotRead(BaseModel):
    slot_no: int
    qty: int
    targets: list[BundleSlotTargetRead]


class CampaignCreateRequest(BaseModel):
    """建立活動（DRAFT）。寄售折扣預設關；品項預設自有序號+自有散裝開（docs/21 §8）。

    寄售品若開折扣（applies_consignment），一律按比例分攤——寄售人按折後價分潤（docs/21 §8.1）。
    """

    name: Annotated[str, Field(min_length=1, max_length=100)]
    # 活動類型（docs/40 P2）：打折填 discount_pct、指定特價填 fixed_price、每件折金額填 amount_off，
    # 只能填對應的那一個。沒帶 kind＝打折（舊客戶端相容）。
    kind: CampaignKind = CampaignKind.PERCENT_OFF
    discount_pct: Annotated[int, Field(ge=1, le=99)] | None = None
    fixed_price: NTDPositive | None = None
    amount_off: NTDPositive | None = None
    # 買 N 送 M（docs/40 P3）：兩個都要填；寄售品一律不參加（裁示 7）。
    buy_qty: PromoQty | None = None
    free_qty: PromoQty | None = None
    # 組合價（docs/40 P4）：整組含稅價＋至少一個格子；寄售品不進組合包（裁示 7）。
    bundle_price: NTDPositive | None = None
    bundle_slots: Annotated[list[BundleSlotInput], Field(max_length=BUNDLE_SLOTS_MAX)] = []
    starts_at: AwareDateTime
    ends_at: AwareDateTime
    applies_owned_serialized: bool = True
    applies_owned_bulk: bool = True
    applies_catalog: bool = False
    applies_consignment: bool = False
    # v2（docs/40）：可與其他活動疊加；false＝不跟任何活動併用（定價挑最划算）。
    stackable: bool = False
    # 範圍條件；沒有任何「包含」＝上面勾的種類全部適用。
    targets: Annotated[list[CampaignTargetInput], Field(max_length=CAMPAIGN_TARGETS_MAX)] = []

    @model_validator(mode="after")
    def _kind_matches_value(self) -> Self:
        values: dict[CampaignKind, tuple[object, ...]] = {
            CampaignKind.PERCENT_OFF: (self.discount_pct,),
            CampaignKind.FIXED_PRICE: (self.fixed_price,),
            CampaignKind.AMOUNT_OFF: (self.amount_off,),
            CampaignKind.BUY_N_GET_M: (self.buy_qty, self.free_qty),
            CampaignKind.BUNDLE: (self.bundle_price,),
        }
        if any(v is None for v in values[self.kind]):
            raise ValueError("請填這種活動的數值（折扣／特價／折金額／買幾送幾）")
        if any(v is not None for k, vs in values.items() if k != self.kind for v in vs):
            raise ValueError("只能填這種活動的數值，其他類型的欄位請留空")
        if self.kind == CampaignKind.BUY_N_GET_M and self.applies_consignment:
            raise ValueError("寄售品不能參加買 N 送 M")
        if self.kind != CampaignKind.BUNDLE and self.bundle_slots:
            raise ValueError("只有組合價活動可以設定組合內容")
        if self.kind == CampaignKind.BUNDLE:
            self._check_bundle()
        return self

    def _check_bundle(self) -> None:
        if len(self.bundle_slots) < BUNDLE_SLOTS_MIN:
            raise ValueError(f"組合價至少要有 {BUNDLE_SLOTS_MIN} 樣商品")
        units = sum(slot.qty for slot in self.bundle_slots)
        assert self.bundle_price is not None
        if self.bundle_price < units:
            raise ValueError(f"組合價不能低於 {units} 元（每件至少 1 元）")
        if self.applies_consignment:
            raise ValueError("寄售品不能進組合包")


class CampaignRead(BaseModel):
    id: int
    store_id: int
    name: str
    kind: CampaignKind
    discount_pct: int | None
    fixed_price: NTDAmountOpt = None
    amount_off: NTDAmountOpt = None
    buy_qty: int | None = None
    free_qty: int | None = None
    bundle_price: NTDAmountOpt = None
    bundle_slots: list[BundleSlotRead] = []
    applies_owned_serialized: bool
    applies_owned_bulk: bool
    applies_catalog: bool
    applies_consignment: bool
    starts_at: datetime
    ends_at: datetime
    status: CampaignStatus
    stackable: bool
    targets: list[CampaignTargetRead]
    created_by: int
    created_at: datetime
    updated_at: datetime


# ── 組合包袋裝條碼（ADR-028）──

# 一袋最多幾項（不同商品）；件數另由每項的 qty（1–99）限制。
BUNDLE_PACK_ITEMS_MAX = 50


class BundlePackItemInput(BaseModel):
    """袋裡的一項：一般商品／販售籃 × 件數，或一件序號品（件數只能 1）。"""

    item_type: BundlePackItemType
    target_id: Annotated[int, Field(gt=0)]
    qty: PromoQty = 1

    @model_validator(mode="after")
    def _serialized_is_single(self) -> Self:
        if self.item_type is BundlePackItemType.SERIALIZED and self.qty != 1:
            raise ValueError("序號品一袋只能放 1 件")
        return self


class BundlePackCreateRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    items: Annotated[
        list[BundlePackItemInput], Field(min_length=1, max_length=BUNDLE_PACK_ITEMS_MAX)
    ]


class BundlePackItemRead(BaseModel):
    item_type: BundlePackItemType
    target_id: int
    qty: int
    label: str
    """顯示用名稱（序號品含條碼）。"""


class BundlePackRead(BaseModel):
    id: int
    store_id: int
    campaign_id: int
    code: str
    name: str
    is_active: bool
    created_at: AwareDateTime
    items: list[BundlePackItemRead]


class BundlePackScanItemRead(BaseModel):
    """POS 掃袋裝條碼時袋裡一項的現況：前端據此一件件加進購物車。"""

    item_type: BundlePackItemType
    target_id: int
    qty: int
    code: str
    """這項商品自己的條碼（序號品 item_code、一般商品 sku、販售籃 code）。"""
    name: str
    unit_price: NTDAmountOpt
    note: str | None
    brand_id: int | None
    stock: int
    """目前可賣幾件（序號品在庫為 1、否則 0）：前端當購物車的數量上限。"""
    available: bool
    unavailable_reason: str | None


class BundlePackScanRead(BaseModel):
    id: int
    code: str
    name: str
    campaign_id: int
    campaign_name: str
    bundle_price: NTDAmountOpt
    campaign_effective: bool
    """所屬組合價活動現在生效中；否則照原價計（前端要提示）。"""
    items: list[BundlePackScanItemRead]
