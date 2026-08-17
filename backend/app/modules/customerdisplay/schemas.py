"""客顯裝置、櫃檯與配對 API schema。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.pricing import DiscountRequest
from app.modules.sales.schemas import SaleAdjustmentRequest
from app.shared.enums import (
    AdjustmentScope,
    CalculationMethod,
    CartSessionStatus,
    SaleLineKind,
    SaleLineType,
    ServiceMode,
    SignatureTaskStatus,
    TenderType,
)


class KioskDeviceLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    installation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    label: str = Field(min_length=1, max_length=100)


class KioskSummary(BaseModel):
    id: int
    label: str
    online: bool
    last_seen_at: datetime | None
    current_session_id: int | None
    displayed_revision: int


class TerminalSummary(BaseModel):
    id: int
    name: str


class KioskDeviceSessionRead(BaseModel):
    device_id: int
    label: str
    csrf_token: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: TerminalSummary | None


class KioskDeviceRead(BaseModel):
    device_id: int
    label: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: TerminalSummary | None


class KioskHeartbeatRequest(BaseModel):
    current_session_id: int | None = Field(default=None, ge=1)
    displayed_revision: Annotated[int, Field(ge=0)]


class KioskHeartbeatRead(BaseModel):
    online: bool
    last_seen_at: datetime


class TerminalCreateRequest(BaseModel):
    installation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    name: str = Field(min_length=1, max_length=100)


class TerminalRead(BaseModel):
    id: int
    installation_id: str
    name: str
    paired_kiosk: KioskSummary | None


class TerminalPairRequest(BaseModel):
    pairing_code: str = Field(pattern=r"^\d{6}$")


class TerminalUnpairRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class CartLineRequest(BaseModel):
    line_type: SaleLineType
    item_code: str | None = Field(default=None, min_length=1, max_length=64)
    catalog_product_id: int | None = Field(default=None, ge=1)
    bulk_lot_id: int | None = Field(default=None, ge=1)
    menu_item_id: int | None = Field(default=None, ge=1)
    qty: int = Field(default=1, ge=1)
    # 商業性質與贈品來歷：客顯購物車是權威購物車，贈品必須經同一條路徑進來，
    # 否則快照與實際成交會對不起來（結帳時逐欄位比對會失敗）。
    line_kind: SaleLineKind = SaleLineKind.NORMAL
    gift_reason_id: int | None = Field(default=None, ge=1)
    gift_note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _matching_reference(self) -> "CartLineRequest":
        refs = {
            SaleLineType.SERIALIZED: self.item_code,
            SaleLineType.CATALOG: self.catalog_product_id,
            SaleLineType.BULK_LOT: self.bulk_lot_id,
            SaleLineType.MENU: self.menu_item_id,
        }
        if refs[self.line_type] is None:
            raise ValueError(f"{self.line_type.value} 明細缺少對應商品識別")
        if self.line_type is SaleLineType.SERIALIZED and self.qty != 1:
            raise ValueError("序號品數量固定為 1")
        return self

    def to_input(self) -> SaleLineInput:
        return SaleLineInput(
            line_type=self.line_type,
            item_code=self.item_code,
            catalog_product_id=self.catalog_product_id,
            bulk_lot_id=self.bulk_lot_id,
            menu_item_id=self.menu_item_id,
            qty=self.qty,
            line_kind=self.line_kind,
            gift_reason_id=self.gift_reason_id,
            gift_note=self.gift_note,
        )


class CartTenderRequest(BaseModel):
    tender_type: TenderType
    amount: Decimal = Field(gt=0)

    @field_validator("amount")
    @classmethod
    def _whole_ntd(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("付款金額必須為整數元")
        return value


class CartUpsertRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    lines: list[CartLineRequest] = Field(min_length=1)
    buyer_contact_id: int | None = Field(default=None, ge=1)
    tenders: list[CartTenderRequest] | None = Field(default=None, min_length=1, max_length=2)
    # 臨時折扣：客顯購物車是權威購物車，折扣必須經同一條路徑進來，否則客人螢幕上看到的
    # 金額與實際結帳不同，且結帳時的快照比對會直接失敗。
    adjustments: list[SaleAdjustmentRequest] | None = None
    # 餐飲內用/外帶與桌號（docs/35）：**必須跟著購物車保存**，否則 POS 重新載入被凍結的
    # 購物車時選擇會遺失；而凍結中兩顆模式鍵都是停用的，已簽名的交易就只能作廢重簽。
    service_mode: ServiceMode | None = None
    table_no: str | None = Field(default=None, max_length=20)

    @field_validator("table_no")
    @classmethod
    def _normalize_table_no(cls, value: str | None) -> str | None:
        """與 `SaleCreateRequest` 同一條正規化規則（去空白、空字串視同未填）。"""
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _check_adjustment_targets(self) -> "CartUpsertRequest":
        for adjustment in self.adjustments or []:
            index = adjustment.target_line_index
            if index is not None and index >= len(self.lines):
                raise ValueError(f"要折扣的商品不在購物車內（第 {index + 1} 項）")
        return self

    def to_adjustments(self) -> list[DiscountRequest] | None:
        if self.adjustments is None:
            return None
        return [adjustment.to_request() for adjustment in self.adjustments]


class CartCancelRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


class CartFreezeRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class CartBeginCheckoutRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    signature_task_id: int | None = Field(default=None, ge=1)


class CartItemRead(BaseModel):
    item_key: str
    line_type: SaleLineType
    # 贈品在客人螢幕上要看得出來是贈品（成交 0 元），不是被折到 0 的商品。
    line_kind: SaleLineKind
    name: str
    qty: int
    unit_price: str
    original_unit_price: str | None
    discount_amount: str
    manual_discount_amount: str
    line_total: str
    net_amount: str


class MaskedMemberRead(BaseModel):
    display_name: str


class CartTenderRead(BaseModel):
    tender_type: TenderType
    amount: str


class CartSnapshotRead(BaseModel):
    content_version: str
    items: list[CartItemRead]
    total: str
    discount_total: str
    # 贈品原價價值僅供顯示參考：不加進應付、也不算折扣。
    gift_retail_value: str
    manual_discount_total: str
    campaign_name: str | None
    member: MaskedMemberRead | None
    tenders: list[CartTenderRead]


class CartChangeRead(BaseModel):
    type: str
    item_key: str
    name: str
    from_qty: int | None = None
    to_qty: int | None = None


class CartSessionRead(BaseModel):
    id: int
    status: CartSessionStatus
    revision: int
    pos_terminal_id: int
    kiosk_device_id: int
    snapshot: CartSnapshotRead
    changes: list[CartChangeRead]
    created_at: datetime
    updated_at: datetime


class KioskCartSessionRead(BaseModel):
    """客顯渲染所需最小購物車視圖；不暴露櫃檯／裝置內部識別。"""

    id: int
    status: CartSessionStatus
    revision: int
    snapshot: CartSnapshotRead
    changes: list[CartChangeRead]
    updated_at: datetime


class StaffCartLineRead(BaseModel):
    """還原用的明細（含贈品來歷）。**刻意與 `CartLineRequest` 分開**：請求模型若同時出現在
    回應裡，OpenAPI 會分裂出 Input/Output 兩個變體並連帶改名既有 schema，前端合約整片位移。"""

    line_type: SaleLineType
    item_code: str | None = None
    catalog_product_id: int | None = None
    bulk_lot_id: int | None = None
    menu_item_id: int | None = None
    qty: int
    line_kind: SaleLineKind
    gift_reason_id: int | None = None
    gift_note: str | None = None


class StaffCartAdjustmentRead(BaseModel):
    """還原用的折扣意圖。"""

    scope: AdjustmentScope
    method: CalculationMethod
    value: str
    target_line_index: int | None = None
    reason_id: int | None = None
    note: str | None = None


class StaffCartPayloadRead(BaseModel):
    """POS 還原購物車所需的原始請求內容。"""

    lines: list[StaffCartLineRead]
    adjustments: list[StaffCartAdjustmentRead] = []
    # 餐飲內用/外帶與桌號（docs/35）；舊購物車沒有這兩欄 → None。
    service_mode: ServiceMode | None = None
    table_no: str | None = None

    @field_validator("adjustments", mode="before")
    @classmethod
    def _null_adjustments_is_empty(cls, value: object) -> object:
        """落盤的請求在沒有折扣時是 `adjustments: null`；讀取端要接得住，
        否則整個回應會驗證失敗（購物車就再也讀不出來了）。"""
        return [] if value is None else value


class StaffCartSessionRead(CartSessionRead):
    """POS 恢復工作階段所需的內部資料；客顯 response model 絕不包含這些欄位。"""

    # 還原用的原始請求（贈品原因／備註、折扣意圖都在這裡；客顯快照沒有）。
    # 舊購物車沒有這份資料 → null，POS 退回只以快照重建（會少掉贈品與折扣，故不得回寫）。
    staff_payload: StaffCartPayloadRead | None = None
    buyer_contact_id: int | None
    active_signature_task_id: int | None
    payment_order_id: str | None
    payment_uncertain_at: datetime | None
    payment_uncertain_reason: str | None
    sale_id: int | None


class CartFreezeRead(BaseModel):
    cart: StaffCartSessionRead
    signature_task_id: int
    signature_status: SignatureTaskStatus
    expires_at: datetime


class PaymentReconciliationRequest(BaseModel):
    action: Literal["QUERY_PROVIDER", "MANUAL_SUCCESS", "MANUAL_FAILED"]
    reason: str | None = Field(default=None, max_length=300)
    evidence_type: str | None = Field(default=None, max_length=60)
    evidence_reference: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _manual_requires_audit_evidence(self) -> "PaymentReconciliationRequest":
        if self.action.startswith("MANUAL_") and not all(
            (
                self.reason and self.reason.strip(),
                self.evidence_type and self.evidence_type.strip(),
                self.evidence_reference and self.evidence_reference.strip(),
            )
        ):
            raise ValueError("人工裁定必須填寫原因、外部證據類型與交易識別")
        return self


class PaymentReconciliationRead(BaseModel):
    outcome: Literal["SUCCESS_CONFIRMED", "FAILED_CONFIRMED", "STILL_UNCERTAIN"]
    cart: StaffCartSessionRead
