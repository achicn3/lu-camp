"""sales 的 Pydantic schema：結帳請求與輸出（§11 合約）。

金額以字串傳輸（§11）、新台幣整數元（§6）。明細依 line_type 擇一帶參照，於 service 解析。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.modules.sales.inputs import InvoiceInfoInput, SaleLineInput, TenderInput
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.shared.enums import (
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
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

    @model_validator(mode="after")
    def _check_shape(self) -> "SaleLineCreateRequest":
        """依 line_type 驗證：只接受對應的參照、序號品 qty 必為 1（避免靜默只賣 1）。"""
        if self.line_type == SaleLineType.SERIALIZED:
            if self.item_code is None:
                raise ValueError("SERIALIZED 明細必須帶 item_code")
            if self.catalog_product_id is not None or self.bulk_lot_id is not None or (
                self.menu_item_id is not None
            ):
                raise ValueError("SERIALIZED 明細只能帶 item_code")
            if self.qty != 1:
                raise ValueError("SERIALIZED 明細數量必須為 1")
        elif self.line_type == SaleLineType.CATALOG:
            if self.catalog_product_id is None:
                raise ValueError("CATALOG 明細必須帶 catalog_product_id")
            if self.item_code is not None or self.bulk_lot_id is not None or (
                self.menu_item_id is not None
            ):
                raise ValueError("CATALOG 明細只能帶 catalog_product_id")
        elif self.line_type == SaleLineType.MENU:
            if self.menu_item_id is None:
                raise ValueError("MENU 明細必須帶 menu_item_id")
            if self.item_code is not None or self.catalog_product_id is not None or (
                self.bulk_lot_id is not None
            ):
                raise ValueError("MENU 明細只能帶 menu_item_id")
        else:  # BULK_LOT
            if self.bulk_lot_id is None:
                raise ValueError("BULK_LOT 明細必須帶 bulk_lot_id")
            if self.item_code is not None or self.catalog_product_id is not None or (
                self.menu_item_id is not None
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
        )


class SaleTenderRequest(BaseModel):
    """單筆收款明細輸入（SC-3）：金額以字串傳輸（§11）、整數元、>0。"""

    tender_type: TenderType
    amount: NTDAmount

    @field_validator("amount")
    @classmethod
    def _positive_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("金額必須為整數元")
        if value <= 0:
            raise ValueError("金額必須為正")
        return value

    def to_input(self) -> TenderInput:
        return TenderInput(tender_type=self.tender_type, amount=self.amount)


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
        chosen = [
            v for v in (self.buyer_tax_id, self.mobile_carrier, self.npoban) if v is not None
        ]
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
    # 發票資訊（docs/24）：einvoice_enabled 時 POS 可帶統編/載具/捐贈碼；省略＝B2C 一般開立。
    invoice: SaleInvoiceInfoRequest | None = None

    def to_inputs(self) -> list[SaleLineInput]:
        return [line.to_input() for line in self.lines]

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

    def to_inputs(self) -> list[SaleLineInput]:
        return [line.to_input() for line in self.lines]


class SaleQuoteLineRead(BaseModel):
    """試算單行輸出：折後實際成交＋折讓留痕。"""

    line_type: SaleLineType
    description: str
    qty: int
    unit_price: NTDAmount
    line_total: NTDAmount
    original_unit_price: NTDAmountOpt
    discount_amount: NTDAmount


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


class SaleTenderRead(BaseModel):
    """收款明細輸出（SC-3）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_type: TenderType
    amount: NTDAmount


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
    lines: list[SaleLineRead] = []
    tenders: list[SaleTenderRead] = []
    # 本單活動折讓總額（docs/21）＝Σ 各行 discount_amount；供明細聯/收據顯示。
    total_discount: NTDAmount = Decimal(0)

    @classmethod
    def build(
        cls, sale: Sale, lines: list[SaleLine], tenders: list[SaleTender] | None = None
    ) -> "SaleRead":
        data = cls.model_validate(sale)
        line_reads = [SaleLineRead.model_validate(line) for line in lines]
        total_discount = sum((line.discount_amount for line in line_reads), Decimal(0))
        return data.model_copy(
            update={
                "lines": line_reads,
                "tenders": [SaleTenderRead.model_validate(t) for t in (tenders or [])],
                "total_discount": total_discount,
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
    # 買方會員（docs/23 K5b）：有買方的單才能推「交易紀錄簽收」至手持裝置。
    buyer_contact_id: int | None
