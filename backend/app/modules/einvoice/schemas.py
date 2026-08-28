"""einvoice 查詢/操作 schema（金額字串整數元，§6/§11）。"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.core.money import ensure_ntd_fits_numeric_12
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceIssueChannel,
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
    random_number: str | None
    # Amego 回傳的證明聯列印內容（docs/24）；查詢復原的發票為 None（證明聯不可印）。
    barcode_text: str | None
    qrcode_left: str | None
    qrcode_right: str | None
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
    # 開立來源（docs/36）：MANUAL_PAPER 代表手開紙本，前端據此隱藏作廢/折讓/印證明聯。
    issue_channel: EInvoiceIssueChannel
    created_at: datetime


class InvoiceReprintPayloadRead(BaseModel):
    """證明聯列印內容（base64 的 ESC/POS，由 Amego `invoice_print` 產生）。

    刻意**只回位元組、不回結構化欄位**：這張版面由加值中心產生，我們不解讀也不改寫——
    任何加工都可能讓二維條碼掃不出來。
    """

    base64_data: str
    # True＝這張會印出「補印」二字（依法須併同原聯兌獎）；False＝正本。
    # 由後端依「證明聯印出來過沒有」決定，不讓前端自己猜。
    is_reprint: bool


class EInvoiceQueueItemRead(BaseModel):
    """上傳佇列項目輸出（GET /einvoice/queue）。

    `invoice_no`／`sale_id` 是給人看的識別（內部 `invoice_id` 對店長毫無意義）：
    佇列頁要能讓店長一眼認出「是哪一張發票、哪一筆交易」才有辦法處置。
    尚未取號的開立列 `invoice_no` 為 None。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    action: EInvoiceAction
    message_type: EInvoiceMessageType
    invoice_id: int | None
    invoice_no: str | None = None
    sale_id: int | None = None
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
    """佇列清單（分頁）。

    `total` 是**符合篩選條件的總筆數**（不受分頁限制）：導覽列的待處理紅點要靠它，
    只數本頁筆數會在超過一頁時謊報。
    """

    items: list[EInvoiceQueueItemRead]
    total: int
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


class ManualInvoiceRegisterRequest(BaseModel):
    """登記手開紙本備用發票（docs/36）。

    只登記**客人手上那張紙**的內容，不改金額：`total` 必須等於發票既有總額，否則拒絕
    （登記手開發票不是改金額的後門）。`invoice_time`／`random_number` 可省略——紙本上
    不一定寫得清楚，不強迫店員亂填。
    """

    # 字軌 2 碼 + 號碼 8 碼。**必須用 [0-9] 而非 \d**：Python/Pydantic 的 `\d` 接受
    # Unicode 數字，`ZA１２３４５６７８`（全形）與 `ZA١٢٣٤٥٦٧٨` 都會通過並原樣落庫，
    # 而 PostgreSQL 唯一索引把它們與 ASCII 版視為不同號碼 → 對帳永遠對不起來。
    invoice_no: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    invoice_date: date
    invoice_time: time | None = None

    @field_validator("invoice_time")
    @classmethod
    def _plain_hms(cls, value: time | None) -> time | None:
        """只收 HH:MM:SS：`invoices.invoice_time` 是 VARCHAR(8)。

        Pydantic 的 `time` 會收下 `14:32:00.123456`（15 字元）與 `14:32:00+08:00`（14 字元），
        router 的 isoformat() 就會超出欄寬 → PostgreSQL 拒絕 → 未被端點捕捉的 500，
        而不是好好告訴店員格式不對（Codex 對抗審查第二輪 medium）。
        """
        if value is None:
            return None
        if value.tzinfo is not None:
            raise ValueError("開立時間不可帶時區，請填 HH:MM:SS")
        if value.microsecond:
            raise ValueError("開立時間只到秒，請填 HH:MM:SS")
        return value

    random_number: str | None = Field(default=None, pattern=r"^[0-9]{4}$")  # 同上：限 ASCII
    total: NTDAmount
    # 供稽核追溯：為何改開紙本（字軌用完、平台故障…）。
    note: str | None = Field(default=None, max_length=200)

    @field_validator("total")
    @classmethod
    def _valid_total(cls, value: Decimal) -> Decimal:
        if value <= 0 or value != value.to_integral_value():
            raise ValueError("發票總額必須為正整數元")
        ensure_ntd_fits_numeric_12(value, field="發票總額")
        return value
