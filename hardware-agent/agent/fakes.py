"""Fake 裝置實作（Wave 2.0 骨架）。

Fake 與真機驅動實作**同一組 `agent.interfaces` Protocol**，供無實機時的開發與
自動化測試使用。**Fake 不只「假裝成功」**：可依建構參數模擬真實失敗——離線、
缺紙、上蓋開啟、連線逾時、錢櫃未接——讓上層的錯誤處理路徑在沒有實體機器時
也被測到。成功時把動作記錄到 `calls`/`labels`…供測試斷言。
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent.errors import (
    CoverOpen,
    DeviceOffline,
    DeviceTimeout,
    DrawerNotConnected,
    PaperOut,
)
from agent.interfaces import (
    DeviceKind,
    DeviceStatus,
    InvoicePayload,
    SalePayload,
    StoreHeader,
)


class FakeLabelPrinter:
    """標籤機 Fake。`offline`/`timeout` 模擬連線失敗。"""

    def __init__(self, *, offline: bool = False, timeout: bool = False) -> None:
        self.offline = offline
        self.timeout = timeout
        self.labels: list[tuple[str, str, int]] = []

    def print_label(self, code: str, name: str, price: int) -> None:
        if self.timeout:
            raise DeviceTimeout("fake label printer timeout")
        if self.offline:
            raise DeviceOffline("fake label printer offline")
        self.labels.append((code, name, price))


class FakeReceiptPrinter:
    """收據機 Fake。`offline`/`timeout`/`paper_out`/`cover_open` 模擬失敗。"""

    def __init__(
        self,
        *,
        offline: bool = False,
        timeout: bool = False,
        paper_out: bool = False,
        cover_open: bool = False,
    ) -> None:
        self.offline = offline
        self.timeout = timeout
        self.paper_out = paper_out
        self.cover_open = cover_open
        self.receipts: list[tuple[SalePayload, StoreHeader]] = []
        self.details: list[tuple[SalePayload, StoreHeader]] = []
        self.einvoices: list[InvoicePayload] = []

    def _guard(self) -> None:
        if self.timeout:
            raise DeviceTimeout("fake receipt printer timeout")
        if self.offline:
            raise DeviceOffline("fake receipt printer offline")
        if self.cover_open:
            raise CoverOpen("fake receipt printer cover open")
        if self.paper_out:
            raise PaperOut("fake receipt printer out of paper")

    def print_receipt(self, sale: SalePayload, header: StoreHeader) -> None:
        self._guard()
        self.receipts.append((sale, header))

    def print_detail(self, sale: SalePayload, header: StoreHeader) -> None:
        self._guard()
        self.details.append((sale, header))

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        self._guard()
        self.einvoices.append(invoice)


class FakeCashDrawer:
    """錢櫃 Fake。`connected=False` 模擬未接 drawer port。"""

    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.open_count = 0

    def open(self) -> None:
        if not self.connected:
            raise DrawerNotConnected("fake cash drawer not connected")
        self.open_count += 1


class FakeStatusProvider:
    """裝置狀態 Fake：回傳預設的裝置清單，可調 `online` 模擬離線/心跳。"""

    def __init__(self, *, statuses: list[DeviceStatus] | None = None) -> None:
        self._statuses = statuses if statuses is not None else _default_statuses()

    def poll(self) -> list[DeviceStatus]:
        return list(self._statuses)


def _default_statuses() -> list[DeviceStatus]:
    """預設兩台印表機 + 錢櫃皆在線的 Fake 狀態（A 級欄位）。

    `unsupported` 必須**鏡射真機 `RealStatusProvider` 的契約**（ADR-011）：兩台印表機
    B 級（缺紙/上蓋/錯誤）皆不做、錢櫃開關偵測不做，否則 Fake/預設模式與真機模式的
    `/devices/status` 契約會分歧、誤導前端把「不支援」當成「支援」。
    """
    now = datetime.now(UTC)
    printer_unsupported = ["paper_out", "cover_open", "error"]
    return [
        DeviceStatus(
            id="label-1",
            kind=DeviceKind.LABEL_PRINTER,
            model="Brother QL-810W",
            online=True,
            last_seen=now,
            unsupported=list(printer_unsupported),  # 網路下 B 級不做
            driver="fake",
        ),
        DeviceStatus(
            id="receipt-1",
            kind=DeviceKind.RECEIPT_PRINTER,
            model="EPSON TM-T82III",
            online=True,
            last_seen=now,
            unsupported=list(printer_unsupported),  # 產品裁示不做 → unsupported
            driver="fake",
        ),
        DeviceStatus(
            id="drawer-1",
            kind=DeviceKind.CASH_DRAWER,
            model="EPSON drawer port",
            online=True,
            last_seen=now,
            unsupported=["drawer_open"],  # 開/關狀態偵測不做（彈開指令另做）
            driver="fake",
        ),
    ]
