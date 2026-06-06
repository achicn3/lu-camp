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

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


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


class SaleLinePayload(BaseModel):
    """銷售明細行（鏡射後端 `SaleLineRead` JSON）。金額為字串整數元。"""

    line_type: str
    description: str
    qty: int
    unit_price: str
    line_total: str


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


class InvoicePayload(BaseModel):
    """電子發票列印輸入（**介面 placeholder**）。

    實際發票版面與欄位依「發票收尾階段」（docs/14 §5）定案，現在只先佔位、
    保留接縫；T13/T14 完成後再補齊欄位，不在此憑記憶硬寫發票內容。
    """

    sale_id: int


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

    def print_receipt(self, sale: SalePayload) -> None:
        """列印結帳收據聯。"""
        ...

    def print_detail(self, sale: SalePayload) -> None:
        """列印商品明細聯（逐項品名/數量/單價/小計 + 總計/稅）。"""
        ...

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """列印電子發票（介面 placeholder，內容待發票收尾階段）。"""
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
