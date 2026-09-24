"""campaigns API schema（docs/21）。折扣 1-99；金額不在此（活動只存折扣率）。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, Field, PlainSerializer, model_validator

from app.core.money import format_ntd
from app.core.time import AwareDateTime
from app.shared.enums import CampaignKind, CampaignStatus, CampaignTargetMode, CampaignTargetType

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
        }
        if any(v is None for v in values[self.kind]):
            raise ValueError("請填這種活動的數值（折扣／特價／折金額／買幾送幾）")
        if any(v is not None for k, vs in values.items() if k != self.kind for v in vs):
            raise ValueError("只能填這種活動的數值，其他類型的欄位請留空")
        if self.kind == CampaignKind.BUY_N_GET_M and self.applies_consignment:
            raise ValueError("寄售品不能參加買 N 送 M")
        return self


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
