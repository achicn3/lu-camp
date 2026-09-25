"""收購佇列 API schema（docs/42）。金額一律含稅整數元、以字串傳輸（§6、§11）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

from app.core.money import format_ntd
from app.shared.enums import (
    AcquisitionType,
    Grade,
    IntakeBatchStatus,
    IntakeDisposition,
    ItemKind,
    PayoutMethod,
)

NTDInput = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=0)]
NTDOut = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]
NTDOutOpt = Annotated[
    Decimal | None,
    PlainSerializer(lambda d: None if d is None else format_ntd(d), return_type=str | None),
]
ShortName = Annotated[str, Field(min_length=1, max_length=100)]
Note = Annotated[str, Field(max_length=500)]


class IntakeBatchCreateRequest(BaseModel):
    """報到收件：選好賣方、和客人一起點清件數。"""

    contact_id: Annotated[int, Field(gt=0)]
    declared_item_count: Annotated[int, Field(ge=1, le=999)]
    note: Note | None = None


class IntakeLineFields(BaseModel):
    """估價列可填的欄位（新增時全帶；修改時只帶要改的）。每件金額。"""

    short_name: ShortName | None = None
    qty: Annotated[int, Field(ge=1, le=999)] | None = None
    acquisition_type: AcquisitionType | None = None
    reference_price: NTDInput | None = None
    discount_pct: Annotated[int, Field(ge=1, le=100)] | None = None
    expected_listed_price: NTDInput | None = None
    suggested_cost: NTDInput | None = None
    deal_cost: NTDInput | None = None
    commission_pct: Annotated[int, Field(ge=0, le=100)] | None = None
    grade: Grade | None = None
    category_id: Annotated[int, Field(gt=0)] | None = None
    brand_id: Annotated[int, Field(gt=0)] | None = None
    product_model_id: Annotated[int, Field(gt=0)] | None = None
    note: Note | None = None


class IntakeLineCreateRequest(IntakeLineFields):
    short_name: ShortName
    qty: Annotated[int, Field(ge=1, le=999)] = 1
    acquisition_type: AcquisitionType = AcquisitionType.BUYOUT


class IntakeDispositionRequest(BaseModel):
    """叫號時的處置；沒成交的件是否已交還客人一起記。"""

    disposition: IntakeDisposition
    accepted_qty: Annotated[int, Field(ge=0, le=999)] | None = None
    returned_to_customer: bool = False


class IntakeSignatureRequest(BaseModel):
    """送客人簽署：推到哪一台顧客螢幕（多櫃檯時指定；單櫃檯可省略）。"""

    terminal_id: Annotated[int, Field(ge=1)] | None = None


class IntakeSignatureRead(BaseModel):
    signature_task_id: int


class IntakeReceiptItem(BaseModel):
    name: str
    amount: str


class IntakeReceiptRead(BaseModel):
    """整批的收購明細（含簽名）：印給客人的存證聯，內容就是客人在顧客螢幕上簽的那份。

    一批可能成立好幾張收購單（買斷／散裝／寄售各一），`reference` 把單號都列出來；
    `acquisition_id` 給還不認得 `reference` 的舊版硬體代理印單號用。購物金兩欄是整批加總
    撥入額與最後一筆撥入後的帳本餘額（交易當下的事實，不是列印當下另查的餘額）。
    """

    store_id: int
    acquisition_id: int
    reference: str
    seller_name: str
    items: list[IntakeReceiptItem]
    total: str
    payout_method: PayoutMethod
    signed_at: datetime
    signature_task_id: int
    store_credit_granted: NTDOutOpt = None
    store_credit_balance_after: NTDOutOpt = None


# ── 待整理上架（I4，docs/42 §7、§8）──────────────────────────────────


class IntakeAwaitingListingRead(BaseModel):
    """待整理清單的一批：放了幾天、還有幾件沒上架（件數＝序號品 1 件、散裝算整堆件數）。"""

    id: int
    ticket_label: str
    slip_code: str
    contact_name: str
    status: IntakeBatchStatus
    paid_at: datetime
    days_waiting: int
    pending_count: int
    listed_count: int


class IntakeItemRead(BaseModel):
    """這一批付款時建好的一件商品（序號品）或一堆（散裝）。"""

    kind: ItemKind
    id: int
    code: str
    name: str
    line_no: int | None = None
    """來自估價的第幾列：同一列的多件是同款，畫面合成一張卡（品牌型號只填一次）。"""
    consignment: bool
    grade: Grade | None = None
    brand_id: int | None = None
    brand_name: str | None = None
    product_model_id: int | None = None
    product_model_name: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    listed_price: NTDOut
    """售價（散裝＝每件售價）。"""
    qty: int
    acquisition_cost: NTDOutOpt = None
    """每件收購成本（客人簽過，不能改）；寄售為 None。"""
    retail_price: NTDOutOpt = None
    note: str | None = None
    listed: bool
    missing: list[str]
    """上架前還缺的資料（顯示用；只有分類是上架必填）。"""


class IntakeItemEdit(BaseModel):
    """補資料：只帶要改的欄位；品牌／型號可帶 null 清掉。成本與件數不在這裡。"""

    kind: Literal[ItemKind.SERIALIZED, ItemKind.BULK_LOT]
    id: Annotated[int, Field(gt=0)]
    name: Annotated[str, Field(min_length=1, max_length=150)] | None = None
    grade: Grade | None = None
    brand_id: int | None = None
    product_model_id: int | None = None
    category_id: int | None = None
    listed_price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=0)] | None = None
    note: Note | None = None


class IntakeListingRequest(BaseModel):
    """`publish`＝這些件同時上架（轉成可賣）；否則只存資料、留在待整理。"""

    items: Annotated[list[IntakeItemEdit], Field(min_length=1, max_length=1000)]
    publish: bool = False


class IntakeDiscrepancyRequest(BaseModel):
    """上架時發現少件或壞到不能賣。二手商品一件一件記（qty 固定 1）；散裝記少了幾件。"""

    kind: Literal[ItemKind.SERIALIZED, ItemKind.BULK_LOT]
    id: Annotated[int, Field(gt=0)]
    qty: Annotated[int, Field(ge=1, le=999)] = 1
    reason: Annotated[str, Field(min_length=1, max_length=200)]


class IntakeDiscrepancyRead(BaseModel):
    id: int
    kind: ItemKind
    item_id: int
    name: str
    qty: int
    reason: str
    created_at: datetime


class IntakeListingResult(BaseModel):
    batch_status: IntakeBatchStatus
    listed: list[IntakeItemRead]
    """這次才上架的件（前端拿去印標籤）；原本就上架的不列、不重印。"""


class IntakePayRequest(BaseModel):
    """付款方式：現金或購物金。有簽署時以客人在顧客螢幕選的為準，這個值不採用。"""

    payout_method: PayoutMethod = PayoutMethod.CASH


class IntakeCancelRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=200)]


class IntakeLineRead(BaseModel):
    id: int
    line_no: int
    short_name: str
    qty: int
    acquisition_type: AcquisitionType
    reference_price: NTDOutOpt = None
    discount_pct: int | None = None
    expected_listed_price: NTDOutOpt = None
    suggested_cost: NTDOutOpt = None
    deal_cost: NTDOutOpt = None
    commission_pct: int | None = None
    grade: Grade | None = None
    category_id: int | None = None
    brand_id: int | None = None
    product_model_id: int | None = None
    note: str | None = None
    disposition: IntakeDisposition
    accepted_qty: int
    returned_to_customer: bool


class IntakeBatchRead(BaseModel):
    id: int
    ticket_date: date
    ticket_no: int
    ticket_label: str
    """畫面與收件單顯示的當日號碼（A001）。"""
    slip_code: str
    """收件單條碼內容（永久唯一，掃了直接打開這一批）。"""
    contact_id: int
    contact_name: str
    declared_item_count: int
    status: IntakeBatchStatus
    note: str | None = None
    created_at: datetime
    cancel_reason: str | None = None
    line_count: int
    """幾項。"""
    item_count: int
    """幾件（各列數量加總）。"""
    deal_total: NTDOut
    """估價的收購總額（成交價 × 數量；寄售不付收購款）。"""
    accepted_item_count: int
    accepted_total: NTDOut
    """叫號確認後、接受的件數與收購總額。"""
    signature_task_id: int | None = None
    """送出的簽署（整批一份）；沒送過為 None。"""
    paid_at: datetime | None = None
    acquisition_ids: list[int] = []
    """付款時依類型成立的收購。"""
    lines: list[IntakeLineRead]
