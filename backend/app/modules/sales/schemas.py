"""sales 的 Pydantic schema：結帳請求與輸出（§11 合約）。

金額以字串傳輸（§11）、新台幣整數元（§6）。明細依 line_type 擇一帶參照，於 service 解析。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.modules.sales.inputs import InvoiceInfoInput, SaleLineInput, TenderInput
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.sales.pricing import DiscountRequest
from app.shared.enums import (
    AdjustmentScope,
    CalculationMethod,
    EInvoiceIssueChannel,
    LinePayRefundStatus,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineKind,
    SaleLineType,
    SaleStatus,
    ServiceMode,
    TenderType,
)

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class SaleLineCreateRequest(BaseModel):
    """單行結帳輸入：SERIALIZED→item_code（qty 固定 1）；CATALOG/BULK_LOT/MENU→id + qty。"""

    line_type: SaleLineType
    item_code: str | None = None
    catalog_product_id: int | None = None
    bulk_lot_id: int | None = None
    menu_item_id: int | None = None
    qty: int = Field(default=1, ge=1)
    # 商業性質與贈品來歷。與客顯購物車的 CartLineRequest 一致——兩條路徑最終都變成
    # 同一個 SaleLineInput，欄位若有落差，購物車快照與實際成交就會對不起來。
    line_kind: SaleLineKind = SaleLineKind.NORMAL
    gift_reason_id: int | None = Field(default=None, ge=1)
    gift_note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_shape(self) -> "SaleLineCreateRequest":
        """依 line_type 驗證：只接受對應的參照、序號品 qty 必為 1（避免靜默只賣 1）。"""
        if self.line_type == SaleLineType.SERIALIZED:
            if self.item_code is None:
                raise ValueError("SERIALIZED 明細必須帶 item_code")
            if (
                self.catalog_product_id is not None
                or self.bulk_lot_id is not None
                or (self.menu_item_id is not None)
            ):
                raise ValueError("SERIALIZED 明細只能帶 item_code")
            if self.qty != 1:
                raise ValueError("SERIALIZED 明細數量必須為 1")
        elif self.line_type == SaleLineType.CATALOG:
            if self.catalog_product_id is None:
                raise ValueError("CATALOG 明細必須帶 catalog_product_id")
            if (
                self.item_code is not None
                or self.bulk_lot_id is not None
                or (self.menu_item_id is not None)
            ):
                raise ValueError("CATALOG 明細只能帶 catalog_product_id")
        elif self.line_type == SaleLineType.MENU:
            if self.menu_item_id is None:
                raise ValueError("MENU 明細必須帶 menu_item_id")
            if (
                self.item_code is not None
                or self.catalog_product_id is not None
                or (self.bulk_lot_id is not None)
            ):
                raise ValueError("MENU 明細只能帶 menu_item_id")
        else:  # BULK_LOT
            if self.bulk_lot_id is None:
                raise ValueError("BULK_LOT 明細必須帶 bulk_lot_id")
            if (
                self.item_code is not None
                or self.catalog_product_id is not None
                or (self.menu_item_id is not None)
            ):
                raise ValueError("BULK_LOT 明細只能帶 bulk_lot_id")
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


class SaleTenderRequest(BaseModel):
    """單筆收款明細輸入（SC-3）：金額以字串傳輸（§11）、整數元、>0。

    line_pay_one_time_key（docs/30 P2）：LINE_PAY 專用，店家掃客人 My Code 得到的一次性付款碼；
    僅 LINE_PAY 需要（其他型別帶入即拒）。單次使用、會過期，不寫入 log/稽核。
    """

    tender_type: TenderType
    amount: NTDAmount
    line_pay_one_time_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("amount")
    @classmethod
    def _positive_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("金額必須為整數元")
        if value <= 0:
            raise ValueError("金額必須為正")
        return value

    @model_validator(mode="after")
    def _one_time_key_only_for_line_pay(self) -> "SaleTenderRequest":
        if self.line_pay_one_time_key is not None and self.tender_type != TenderType.LINE_PAY:
            raise ValueError("line_pay_one_time_key 僅適用於 LINE_PAY 收款")
        return self

    def to_input(self) -> TenderInput:
        return TenderInput(
            tender_type=self.tender_type,
            amount=self.amount,
            line_pay_one_time_key=self.line_pay_one_time_key,
        )


class SaleInvoiceInfoRequest(BaseModel):
    """結帳的發票資訊（docs/24）：買方統編（＝B2B）、手機條碼載具、捐贈碼。

    互斥：統編/載具/捐贈三者至多一項——B2B 發票不掛個人載具、營業人發票不得捐贈、
    載具與捐贈擇一。載具目前僅收手機條碼（`/` 開頭＋7 碼，CarrierType 3J0002）。
    """

    buyer_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    buyer_name: str | None = Field(default=None, min_length=1, max_length=60)
    mobile_carrier: str | None = Field(default=None, pattern=r"^/[0-9A-Z+\-.]{7}$")
    npoban: str | None = Field(default=None, pattern=r"^\d{3,7}$")

    @model_validator(mode="after")
    def _mutually_exclusive(self) -> "SaleInvoiceInfoRequest":
        chosen = [v for v in (self.buyer_tax_id, self.mobile_carrier, self.npoban) if v is not None]
        if len(chosen) > 1:
            raise ValueError("統編、載具、捐贈碼至多擇一")
        if self.buyer_name is not None and self.buyer_tax_id is None:
            raise ValueError("買方名稱僅限打統編（B2B）時填寫")
        return self

    def to_input(self) -> InvoiceInfoInput:
        return InvoiceInfoInput(
            buyer_tax_id=self.buyer_tax_id,
            buyer_name=self.buyer_name,
            carrier_type="3J0002" if self.mobile_carrier is not None else None,
            carrier_id=self.mobile_carrier,
            npoban=self.npoban,
        )


class ReasonRead(BaseModel):
    """贈品／折扣原因代碼。`requires_note` 為真時 POS 必須逼店員填備註。

    POS 選單只拿得到啟用中的；管理頁會連停用的一起列出（停用不實刪）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    requires_note: bool
    sort_order: int
    is_active: bool


class ReasonCreateRequest(BaseModel):
    """新增原因代碼。`code` 在同店內唯一，且**不可事後修改**——歷史單據存的是名稱快照，
    但報表以 code 對照分類，改 code 會讓同一件事在報表上斷成兩段。"""

    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=1, max_length=50)
    requires_note: bool = False
    sort_order: int = Field(default=0, ge=0, le=999)


class ReasonUpdateRequest(BaseModel):
    """修改原因代碼。停用不實刪：歷史單據引用過的原因不能因為後台刪掉就消失。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    requires_note: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=999)
    is_active: bool | None = None


class SaleAdjustmentRequest(BaseModel):
    """店員在結帳畫面輸入的一筆臨時折扣。

    目標以**明細順序索引**指定（0、1…）：成交前 sale_line 還沒有 id，而前後端共用
    同一份明細順序。ITEM 必須指定索引，ORDER 不得指定。
    """

    scope: AdjustmentScope
    method: CalculationMethod
    value: Decimal = Field(gt=0)
    target_line_index: int | None = Field(default=None, ge=0)
    reason_id: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check_target(self) -> "SaleAdjustmentRequest":
        if self.scope is AdjustmentScope.ITEM and self.target_line_index is None:
            raise ValueError("單品折扣必須指定要折哪一個商品")
        if self.scope is AdjustmentScope.ORDER and self.target_line_index is not None:
            raise ValueError("整單折扣不可指定單一商品")
        if self.method is CalculationMethod.PERCENTAGE and self.value >= 100:
            raise ValueError("折扣百分比必須小於 100；免費請改用贈品")
        return self

    def to_request(self) -> DiscountRequest:
        return DiscountRequest(
            scope=self.scope,
            method=self.method,
            value=self.value,
            target_key=(
                None if self.target_line_index is None else str(self.target_line_index)
            ),
            reason_id=self.reason_id,
            note=self.note,
        )


def _validate_adjustment_targets(
    adjustments: list[SaleAdjustmentRequest] | None, line_count: int
) -> None:
    """索引必須落在本次明細範圍內——越界在定價層只會得到「商品不存在」的模糊訊息。"""
    for adjustment in adjustments or []:
        index = adjustment.target_line_index
        if index is not None and index >= line_count:
            raise ValueError(f"要折扣的商品不在本次交易明細內（第 {index + 1} 項）")


class SaleCreateRequest(BaseModel):
    """結帳請求。idempotency key 走 HTTP 標頭 Idempotency-Key，不在 body。

    tenders 省略 → service 預設單一 CASH 全額（向後相容）；提供時 Σ amount 必須等於
    伺服器端計算的 total（否則 422），且每種 tender_type 至多一筆。
    """

    lines: list[SaleLineCreateRequest] = Field(min_length=1)
    buyer_contact_id: int | None = None
    tenders: list[SaleTenderRequest] | None = None
    # 購物金扣抵手持簽署（docs/23 K5，D3）：以購物金付款時綁定的已簽 STORE_CREDIT_USE 任務。
    signature_task_id: int | None = None
    # POS 客顯權威購物車；購物金簽署時必填，其他付款由 POS 帶入以完成客顯清場。
    cart_session_id: int | None = Field(default=None, ge=1)
    cart_revision: int | None = Field(default=None, ge=1)
    # 發票資訊（docs/24）：einvoice_enabled 時 POS 可帶統編/載具/捐贈碼；省略＝B2C 一般開立。
    invoice: SaleInvoiceInfoRequest | None = None
    # 結帳當下 POS 觀察到的 einvoice_enabled（Codex 第二十二輪）：後端於結帳交易內
    # 與現值比對，不符 → 409（他端切換設定的 TOCTOU 防護）。舊客戶端可省略。
    expected_einvoice_enabled: bool | None = None
    # 結帳當下套用的臨時折扣（贈品走 lines[].line_kind，不是折扣）。
    adjustments: list[SaleAdjustmentRequest] | None = None
    # 餐飲內用/外帶與桌號（docs/35）：購物車含餐飲明細時必填，否則不得帶。
    # 兩者與購物車內容的相依關係由 service 驗證（此處看不到明細是不是餐飲）。
    service_mode: ServiceMode | None = None
    table_no: Annotated[str, Field(max_length=20)] | None = None

    @field_validator("table_no")
    @classmethod
    def _normalize_table_no(cls, value: str | None) -> str | None:
        """桌號在**邊界**就去空白（空字串視同未填）。

        service 也會正規化，但指紋是 router（重播路徑）與 service（首次）各算一次的：
        兩邊拿到的字串必須完全相同，否則「  A1  」重送會被誤判成同鍵不同內容而 409。
        """
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _check_adjustment_targets(self) -> "SaleCreateRequest":
        _validate_adjustment_targets(self.adjustments, len(self.lines))
        return self

    def to_inputs(self) -> list[SaleLineInput]:
        return [line.to_input() for line in self.lines]

    def to_adjustments(self) -> list[DiscountRequest] | None:
        if self.adjustments is None:
            return None
        return [adjustment.to_request() for adjustment in self.adjustments]

    def to_tender_inputs(self) -> list[TenderInput] | None:
        return None if self.tenders is None else [t.to_input() for t in self.tenders]

    def to_invoice_info(self) -> InvoiceInfoInput | None:
        return None if self.invoice is None else self.invoice.to_input()


NTDAmountOpt = Annotated[
    Decimal | None, PlainSerializer(lambda d: None if d is None else str(d), return_type=str | None)
]


class SaleQuoteRequest(BaseModel):
    """結帳前試算請求（docs/21 C2b）：購物車明細（+買方），回折後總額供 POS 顯示與對齊收款。"""

    lines: list[SaleLineCreateRequest] = Field(min_length=1)
    buyer_contact_id: int | None = None
    adjustments: list[SaleAdjustmentRequest] | None = None

    @model_validator(mode="after")
    def _check_adjustment_targets(self) -> "SaleQuoteRequest":
        _validate_adjustment_targets(self.adjustments, len(self.lines))
        return self

    def to_inputs(self) -> list[SaleLineInput]:
        return [line.to_input() for line in self.lines]

    def to_adjustments(self) -> list[DiscountRequest] | None:
        if self.adjustments is None:
            return None
        return [adjustment.to_request() for adjustment in self.adjustments]


class SaleQuoteLineRead(BaseModel):
    """試算單行輸出：折後實際成交＋折讓留痕。"""

    line_type: SaleLineType
    description: str
    qty: int
    unit_price: NTDAmount
    line_total: NTDAmount
    original_unit_price: NTDAmountOpt
    discount_amount: NTDAmount
    # 商業性質與實付：贈品成交 0 元但仍出庫，臨時折扣則落在 manual_discount_amount。
    line_kind: SaleLineKind
    manual_discount_amount: NTDAmount
    net_amount: NTDAmount


class SaleQuoteResponse(BaseModel):
    """結帳前試算輸出：套生效活動後的折後總額與各行折讓；唯讀。"""

    total: NTDAmount
    campaign_id: int | None
    campaign_name: str | None
    lines: list[SaleQuoteLineRead]
    # 餐飲（內用）小計與購物金可折抵上限（=total−food_subtotal）；POS 據此卡住購物金輸入。
    food_subtotal: NTDAmount
    store_credit_max: NTDAmount
    # 購物金低消門檻（整數元，0＝不限）：非餐飲消費未達此值則完全不可用購物金。
    store_credit_min_spend: NTDAmount
    # 金額摘要：贈品價值**僅供顯示**，不加進應付也不算折扣（活動報表直接 SUM 折扣欄位）。
    gift_retail_value: NTDAmount
    item_discount_amount: NTDAmount
    order_discount_amount: NTDAmount


class SaleLineRead(BaseModel):
    """銷售明細輸出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    line_type: SaleLineType
    serialized_item_id: int | None
    catalog_product_id: int | None
    bulk_lot_id: int | None
    menu_item_id: int | None
    description: str
    qty: int
    unit_price: NTDAmount
    line_total: NTDAmount
    # 門市活動折扣留痕（docs/21）：供明細聯/收據顯示原價與折讓。
    original_unit_price: NTDAmountOpt = None
    discount_amount: NTDAmount = Decimal(0)
    # 商業性質與實付：**退款、發票品項、毛利都認 net_amount**，不是 line_total
    # （line_total 是活動折後的牌價小計，臨時折扣另計在 manual_discount_amount）。
    line_kind: SaleLineKind = SaleLineKind.NORMAL
    manual_discount_amount: NTDAmount = Decimal(0)
    net_amount: NTDAmount = Decimal(0)
    gift_reason_name: str | None = None
    gift_note: str | None = None
    # 已退貨數（退貨頁限額用：可退餘量＝qty−returned_qty；僅 get_sale 端點回填，預設 0）
    returned_qty: int = 0


class SaleTenderRead(BaseModel):
    """收款明細輸出（SC-3）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_type: TenderType
    amount: NTDAmount
    fee_amount: NTDAmount = Decimal(0)  # 支付手續費（店家成本，docs/30）


class SaleRead(BaseModel):
    """銷售單輸出（含明細與收款明細）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    clerk_user_id: int
    buyer_contact_id: int | None
    subtotal: NTDAmount
    tax: NTDAmount
    total: NTDAmount
    payment_method: PaymentMethod
    invoice_status: SaleInvoiceStatus
    status: SaleStatus
    created_at: datetime
    # 餐飲內用（docs/35）：無餐飲明細的銷售兩欄皆 None。純資訊，不進入任何金額計算。
    service_mode: ServiceMode | None = None
    table_no: str | None = None
    lines: list[SaleLineRead] = []
    tenders: list[SaleTenderRead] = []
    # 本單活動折讓總額（docs/21）＝Σ 各行 discount_amount；供明細聯/收據顯示。
    # **只含活動折扣**——臨時折扣與贈品價值另計，混在一起會讓活動報表與收據互相打架。
    total_discount: NTDAmount = Decimal(0)
    # 本單臨時折扣總額＝Σ 各行 manual_discount_amount。
    total_manual_discount: NTDAmount = Decimal(0)
    # 本單贈品原價價值＝Σ 贈品行 original_unit_price × qty；僅供顯示，不計入應付也不算折扣。
    gift_retail_value: NTDAmount = Decimal(0)

    @classmethod
    def build(
        cls,
        sale: Sale,
        lines: list[SaleLine],
        tenders: list[SaleTender] | None = None,
        returned_by_line: dict[int, int] | None = None,
    ) -> "SaleRead":
        data = cls.model_validate(sale)
        line_reads = [
            SaleLineRead.model_validate(line).model_copy(
                update={"returned_qty": (returned_by_line or {}).get(line.id, 0)}
            )
            for line in lines
        ]
        total_discount = sum((line.discount_amount for line in line_reads), Decimal(0))
        total_manual_discount = sum(
            (line.manual_discount_amount for line in line_reads), Decimal(0)
        )
        gift_retail_value = sum(
            (
                (line.original_unit_price or Decimal(0)) * line.qty
                for line in line_reads
                if line.line_kind is SaleLineKind.GIFT
            ),
            Decimal(0),
        )
        return data.model_copy(
            update={
                "lines": line_reads,
                "tenders": [SaleTenderRead.model_validate(t) for t in (tenders or [])],
                "total_discount": total_discount,
                "total_manual_discount": total_manual_discount,
                "gift_retail_value": gift_retail_value,
            }
        )


class SaleSummaryRead(BaseModel):
    """銷售單摘要輸出（列表用，不含明細）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    subtotal: NTDAmount
    tax: NTDAmount
    total: NTDAmount
    invoice_status: SaleInvoiceStatus
    status: SaleStatus
    created_at: datetime
    # 收款方式摘要（docs/30）：作廢/退貨 UI 據此提示台灣Pay 需手動退款、LINE Pay 自動退。
    payment_method: PaymentMethod
    # 買方會員（docs/23 K5b）：有買方的單才能推「交易紀錄簽收」至手持裝置。
    buyer_contact_id: int | None
    # 購物金扣抵簽署（docs/23 K5）：交易列表據此提供一鍵調閱簽名。
    signature_task_id: int | None
    # 餐飲內用/外帶與桌號（docs/35）：交易紀錄要看得出「5 桌點了什麼」。
    service_mode: ServiceMode | None = None
    table_no: str | None = None
    # 發票開立來源（docs/36）：交易紀錄必須在**顯示任何退款指示之前**就知道這筆是不是
    # 手開紙本——否則店員會先被叫去台灣Pay App 退款，送出後才被後端擋下，錢已經出去了。
    # 由 router 經 einvoice service 補上（§2 不跨模組讀表）；無發票 → None。
    invoice_issue_channel: EInvoiceIssueChannel | None = None


class LinePayRefundAttemptRead(BaseModel):
    """未定 LINE Pay 退款嘗試（退款對帳頁；docs/30 finding #3）。不含任何 PII/憑證。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: str
    amount: NTDAmount
    status: LinePayRefundStatus
    created_at: datetime


class LinePayRefundResolveRequest(BaseModel):
    """人工解決未定退款：SUCCEEDED＝已於 LINE Pay 後台確認退款、FAILED＝確認未退款可重試。"""

    resolution: LinePayRefundStatus

    @field_validator("resolution")
    @classmethod
    def _only_terminal(cls, value: LinePayRefundStatus) -> LinePayRefundStatus:
        if value not in (LinePayRefundStatus.SUCCEEDED, LinePayRefundStatus.FAILED):
            raise ValueError("resolution 只能為 SUCCEEDED（已退款）或 FAILED（未退款）")
        return value
