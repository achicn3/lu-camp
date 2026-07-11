"""硬體裝置介面層（Wave 2.0 骨架）。

上層（FastAPI endpoint）只依賴此處的 Protocol 與資料型別，**不依賴任何具體
實作**。Fake（`agent.fakes`）與真機驅動（T15/T16/T18 的 ESC/POS、TCP 探測…）
都實作同一組 Protocol；換 Fake↔真機只換注入到 `agent.devices.AgentDevices`
的實作，endpoint 與路由零改動。

設計原則（CLAUDE.md §9）：
- 介面以行為（列印/開櫃/查狀態）切分，不綁定特定機型或連線方式。
- 失敗以 `agent.errors` 的自訂例外表達（離線/缺紙/逾時/錢櫃未接…），由路由
  層轉成對應 HTTP 狀態；**禁止把失敗吞掉假裝成功**。
- 列印 payload **鏡射後端 `SaleRead` 的 JSON 形狀**（金額為字串、整數元），
  不另立一套，確保與 T11/T12 sales 結構對齊。
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


class DeviceKind(StrEnum):
    """裝置種類（對應 docs/04 `/devices/status` 的 kind）。"""

    LABEL_PRINTER = "LABEL_PRINTER"
    RECEIPT_PRINTER = "RECEIPT_PRINTER"
    SCANNER = "SCANNER"
    CASH_DRAWER = "CASH_DRAWER"


class PaperStatus(StrEnum):
    """缺紙三態（EPSON `paper_status`：足量/將盡/無紙）。B 級用，A 級階段不填。"""

    ADEQUATE = "adequate"
    ENDING = "ending"
    EMPTY = "empty"



# 簽名 PNG 上限（與後端 signing MAX_SIGNATURE_BYTES=512KB 同源）：手寫簽名綽綽有餘，
# 於 payload 邊界即擋炸彈級 base64（解碼端另有解壓 max_length 硬限）。
MAX_SIGNATURE_B64_CHARS = 512_000 * 4 // 3 + 8

class SaleLinePayload(BaseModel):
    """銷售明細行（鏡射後端 `SaleLineRead` JSON）。金額為字串整數元。

    original_unit_price/discount_amount 為門市活動折扣留痕（docs/21）：有折扣時於明細聯顯示
    原價與折讓；無折扣時 original_unit_price=None、discount_amount="0"。代理只如實排版。
    """

    line_type: str
    description: str
    qty: int
    unit_price: str
    line_total: str
    original_unit_price: str | None = None
    discount_amount: str = "0"


class SalePayload(BaseModel):
    """收據／明細聯列印輸入（鏡射後端 `SaleRead` JSON）。

    僅取列印所需欄位；欄位名與後端 `SaleRead`/`SaleLineRead` 一致，後端把
    `SaleRead` 序列化後直接 POST 即相容。金額（subtotal/tax/total/單價/小計）
    皆為字串整數元（§6），代理端不做金額運算、只如實排版。
    """

    id: int
    store_id: int
    subtotal: str
    tax: str
    total: str
    payment_method: str
    invoice_status: str
    created_at: datetime
    lines: list[SaleLinePayload]
    # 門市活動折扣（docs/21）：total_discount 為本單折讓總額（後端算好、代理只印），
    # campaign_name 為套用的活動名；無折扣時 "0"/None。代理不做金額運算。
    total_discount: str = "0"
    campaign_name: str | None = None
    # 購物金×手持簽署（docs/23 K6，D6）：用了購物金且客人簽了扣抵確認時，明細聯加印
    # 折抵/剩餘與簽名影像。金額為字串整數元（後端算好）；簽名為 base64 PNG（8-bit RGBA，
    # 後端 signing 已驗證同一子集）。全為 None 時版面與既有完全相同。
    store_credit_deducted: str | None = None
    store_credit_remaining: str | None = None
    signature_png_base64: str | None = Field(default=None, max_length=MAX_SIGNATURE_B64_CHARS)


class AcquisitionReceiptItem(BaseModel):
    """收購憑證聯品項行（鏡射已簽切結快照的 items）。"""

    name: str
    amount: str


class AcquisitionReceiptPayload(BaseModel):
    """收購憑證聯列印輸入（docs/23 K6）：收購完成後印給賣方的存證聯。

    品項/金額/總額鏡射**已簽切結快照**（後端綁定驗證過的值）；撥款方式為客人手持端所選
    （D7）；選購物金時附撥入金額（含溢價）與撥入後餘額。簽名影像必要——憑證聯的意義
    即簽名存證。金額為字串整數元，代理不做金額運算、只如實排版。
    """

    store_id: int
    acquisition_id: int
    seller_name: str
    items: list[AcquisitionReceiptItem]
    total: str
    payout_method: str  # CASH | STORE_CREDIT
    created_at: datetime
    signature_png_base64: str = Field(max_length=MAX_SIGNATURE_B64_CHARS)
    # 撥入購物金＝簽署凍結溢價的金額（已簽快照值）。
    store_credit_granted: str | None = None
    # 撥入後購物金總額（2026-07-11 裁示加印）＝本筆撥款分錄燒進帳本的 balance_after
    # （後端 AcquisitionResult 回傳、本筆交易的不可變事實）。**仍不收活餘額**（Codex K6
    # 第二輪）：列印當下另查的餘額會隨後續交易漂移，呼叫端不得以它代入本欄。
    store_credit_balance_after: str | None = None


class StoreHeader(BaseModel):
    """收據／明細聯抬頭（店名/統編/地址/電話）。

    **單一事實來源為後端 `stores` 表**（CLAUDE.md §4），由 agent 依 `SalePayload.store_id`
    向後端 `GET /stores/{id}/receipt-header` 取得並快取，不存在 agent 設定檔（避免漂移）。
    收據/明細聯列印時必須印出此抬頭——不印出沒有店名/統編的收據。
    """

    name: str
    tax_id: str | None = None
    address: str | None = None
    phone: str | None = None
    invoice_track_info: str | None = None


class InvoicePayload(BaseModel):
    """電子發票證明聯列印輸入。

    版面依「電子發票實施作業要點」附件一格式一、條碼內容依財政資訊中心
    「電子發票證明聯一維及二維條碼規格說明」v1.9。金額為字串整數元（§6）。
    字軌號碼、隨機碼等取號資料由呼叫端提供（正式來源為後端發票模組，屬
    發票收尾階段 T13/T14；MIG XML 與 Turnkey 上傳亦在該階段，與列印解耦）。
    """

    sale_id: int
    invoice_number: str = Field(pattern=r"^[A-Z]{2}\d{8}$")  # 字軌 2 碼 + 號碼 8 碼
    invoice_date: date
    invoice_time: time
    random_code: str = Field(pattern=r"^(\d{4}| {4})$")  # B2B 為 4 位空白（規格 FAQ 6）
    sales_amount: str = Field(pattern=r"^\d+$")  # 未稅銷售額（整數元）
    tax_amount: str = Field(pattern=r"^\d+$")
    total_amount: str = Field(pattern=r"^\d+$")  # 含稅總計（整數元）
    seller_tax_id: str = Field(pattern=r"^\d{8}$")
    seller_name: str = Field(min_length=1)  # 營業人識別標章（文字）
    buyer_tax_id: str | None = Field(default=None, pattern=r"^\d{8}$")
    lines: list[SaleLinePayload] = Field(min_length=1)

    @field_validator("total_amount")
    @classmethod
    def _total_positive(cls, value: str) -> str:
        """不得開立零元發票（條碼規格第肆章參數說明）。"""
        if int(value) <= 0:
            raise ValueError("總計額必須大於 0（不得開立零元發票）")
        return value


class DeviceStatus(BaseModel):
    """單一裝置狀態（對應 docs/04 / docs/15 §4 回傳）。

    A 級（本階段 T16）：`online` + `last_seen` 心跳保證可填。
    B 級（T17）：`details`（缺紙/上蓋/錯誤/錢櫃）逐機型填，查不到者列 `unsupported`，
    依 ADR-010 不臆造、不當故障。
    `driver`/`validated_on_hardware` 標明此態來自真機驅動或 Fake、是否已實機驗證。
    `probe_error`：探測時遇到的**驅動/套件/設定錯誤**（非單純離線）的如實描述；用以區分
    「裝置離線（online=False、probe_error=None）」與「探測本身失敗（驅動或套件問題）」，
    後者**不可偽裝成單純離線**，前端應顯示錯誤、不誤導店員（ADR-010：不隱藏真實錯誤）。
    """

    id: str
    kind: DeviceKind
    model: str
    online: bool
    last_seen: datetime | None = None
    details: dict[str, str | bool | None] = {}
    unsupported: list[str] = []
    driver: str  # "real" | "fake"
    validated_on_hardware: bool = False
    probe_error: str | None = None


@runtime_checkable
class LabelPrinter(Protocol):
    """標籤機（Brother QL-810W）：列印商品條碼標籤。"""

    def print_label(self, code: str, name: str, price: int) -> None:
        """列印標籤：可讀文字（品名/價格）+ Code 128 條碼（編碼識別碼）。"""
        ...


@runtime_checkable
class ReceiptPrinter(Protocol):
    """收據機（EPSON TM-T82III）：收據聯／商品明細聯／電子發票。"""

    def print_receipt(self, sale: SalePayload, header: StoreHeader) -> None:
        """列印結帳收據聯（含店家抬頭）。"""
        ...

    def print_detail(self, sale: SalePayload, header: StoreHeader) -> None:
        """列印商品明細聯（含店家抬頭；逐項品名/數量/單價/小計 + 總計/稅）。"""
        ...

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """列印電子發票證明聯（附件一格式一：標題/年期別/字軌/一維條碼/雙 QR）。"""
        ...

    def print_acquisition(self, receipt: AcquisitionReceiptPayload, header: StoreHeader) -> None:
        """列印收購憑證聯（docs/23 K6：切結品項/總額/撥款＋客戶簽名影像）。"""
        ...


@runtime_checkable
class CashDrawer(Protocol):
    """錢櫃（接 EPSON drawer port）：踢開錢櫃。"""

    def open(self) -> None:
        """送出 kick 指令踢開錢櫃。"""
        ...


@runtime_checkable
class DeviceStatusProvider(Protocol):
    """裝置狀態來源：回傳所有受管裝置的即時狀態（供 `/devices/status`）。"""

    def poll(self) -> list[DeviceStatus]:
        """輪詢並回傳各裝置目前狀態（A 級保證 online/last_seen）。"""
        ...
