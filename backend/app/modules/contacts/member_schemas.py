"""會員中心（member center）讀取回應 schema（T21-c；docs/17 §5.3）。

金額一律字串整數元（§11；NTDAmount）。不含成本（acquisition_cost）——CLERK 不可見成本
（裁示 #4）。本層唯讀彙整，沿 ReportsService 慣例由 facade 直接回傳。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from app.modules.contacts.schemas import ContactRead

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]
NTDAmountOpt = Annotated[
    Decimal | None,
    PlainSerializer(lambda d: None if d is None else str(d), return_type=str | None),
]


class MemberPurchaseRead(BaseModel):
    """消費紀錄一列（清單摘要）。"""

    sale_id: int
    created_at: datetime
    total: NTDAmount
    payment_method: str
    status: str
    invoice_status: str
    line_count: int


class MemberPurchaseLineRead(BaseModel):
    line_type: str
    description: str
    qty: int
    unit_price: NTDAmount
    line_total: NTDAmount


class MemberPurchaseTenderRead(BaseModel):
    tender_type: str
    amount: NTDAmount


class MemberPurchaseDetailRead(BaseModel):
    """單筆消費明細（lines + tenders）。"""

    sale_id: int
    created_at: datetime
    subtotal: NTDAmount
    tax: NTDAmount
    total: NTDAmount
    payment_method: str
    status: str
    invoice_status: str
    lines: list[MemberPurchaseLineRead]
    tenders: list[MemberPurchaseTenderRead]


class MemberConsignmentRead(BaseModel):
    """寄售品一列；若為已售序號品則帶其結算資訊。"""

    kind: str  # SERIALIZED / BULK_LOT
    code: str  # item_code / lot_code
    name: str
    item_status: str
    commission_pct: int | None
    gross: NTDAmountOpt = None
    commission_amount: NTDAmountOpt = None
    payout_amount: NTDAmountOpt = None
    settlement_status: str | None = None
    sold_date: datetime | None = None


class MemberConsignmentsRead(BaseModel):
    """寄售品清單 + PENDING 應撥加總（裁示 #2）。"""

    items: list[MemberConsignmentRead]
    pending_payout_total: NTDAmount


class MemberSourcedItemRead(BaseModel):
    """會員帶來的商品（買斷+寄售合併清單；不含成本）。"""

    source_type: str  # BUYOUT / CONSIGNMENT
    kind: str  # SERIALIZED / BULK_LOT
    code: str
    name: str
    status: str
    acquisition_id: int | None
    intake_date: datetime
    listed_price: NTDAmount


class MemberOverviewCounts(BaseModel):
    purchases: int
    consigned_items: int


class MemberOverviewRead(BaseModel):
    """會員中心彙整（非全史：計數 + 加總 + 近期摘要；裁示：勿 eager load 全史）。"""

    contact: ContactRead
    member_points: int
    store_credit_balance: NTDAmount
    pending_consignment_payout: NTDAmount
    counts: MemberOverviewCounts
    recent_purchases: list[MemberPurchaseRead]
