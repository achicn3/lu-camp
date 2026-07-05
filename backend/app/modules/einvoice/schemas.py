"""einvoice 查詢/操作 schema（金額字串整數元，§6/§11）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    InvoiceStatus,
    InvoiceType,
    UploadStatus,
)

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class InvoiceRead(BaseModel):
    """發票輸出（GET /invoices/{id}）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    sale_id: int
    invoice_type: InvoiceType
    invoice_no: str | None
    invoice_date: date | None
    invoice_time: str | None
    buyer_tax_id: str | None
    buyer_name: str | None
    carrier_type: str | None
    carrier_id: str | None
    donate_mark: bool
    npoban: str | None
    print_mark: bool
    net: NTDAmount
    tax: NTDAmount
    total: NTDAmount
    status: InvoiceStatus
    created_at: datetime


class EInvoiceQueueItemRead(BaseModel):
    """上傳佇列項目輸出（GET /einvoice/queue）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    action: EInvoiceAction
    message_type: EInvoiceMessageType
    invoice_id: int | None
    allowance_id: int | None
    status: UploadStatus
    attempts: int
    xml_path: str | None
    xml_sha256: str | None
    dropped_at: datetime | None
    uploaded_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class EInvoiceQueueListRead(BaseModel):
    """佇列清單（分頁）。"""

    items: list[EInvoiceQueueItemRead]
    limit: int
    offset: int


class EInvoiceResultRequest(BaseModel):
    """記錄一筆平台回執（手動或 importer）。

    自動解析 Turnkey ProcessResult/SummaryResult 檔的 importer 待收尾階段依 3.9 手冊實作；
    此輸入讓平台結果可先被記錄並驅動佇列/發票狀態。
    """

    success: bool
    kind: str = Field(default="PROCESS", pattern="^(PROCESS|SUMMARY)$")
    status_code: str | None = Field(default=None, max_length=20)
    message: str | None = Field(default=None, max_length=500)
    source_ref: str | None = Field(default=None, max_length=200)
    # 回執所屬交付世代（拋檔檔名 …-a{n}.xml 的 n）：與當前不符 → 409、事件留稽核；
    # **retry 過的佇列（attempts>0）狀態性回執必帶**（省略→409，不得預設為當前世代）。
    # 從未 retry 的列省略無歧義（手動方便）；importer 一律必帶。
    delivery_attempt: int | None = Field(default=None, ge=0)
