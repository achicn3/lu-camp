"""EPSON TM-T82III 網路真機驅動：ESC/POS 位元組寫入 + 錢櫃 kick（測 A wiring）。

`NetworkEscposWriter` 實作 `agent.escpos_printer.SupportsWrite`，把排版好的 ESC/POS
位元組經 **python-escpos `Network` 後端** 送到 EPSON（IP/port/逾時由 `agent.config`
注入，**程式碼不寫死 IP**）。採 **lazy 連線**：每次 `write` 才連線、送出、關閉，agent
啟動不依賴印表機在線。

**錯誤翻譯（D-5、ADR-010 誠實原則）**：連線/逾時等 `OSError` 在此邊界翻成
`agent.errors` 的 `DeviceError`（離線→`DeviceOffline`、逾時→`DeviceTimeout`），由路由
統一 handler 轉對應 HTTP（503/504）——**絕不吞例外假裝成功**。escpos `Network.open()`
連不上時丟 `DeviceNotFoundError`（已包住底層 OSError）；送出階段 `_raw` 的 `sendall`
可能丟 `TimeoutError`／其他 `OSError`。

`RealCashDrawer` 經同一台 EPSON 連線送錢櫃 kick 指令（接在 EPSON drawer port），
錯誤同樣由 writer 邊界翻成 `DeviceError`。

**同一台實體印表機的操作必須排隊（2026-09-15 實測踩過）**：錢櫃跟發票機共用同一條
RJ45（`AGENT_DRAWER_HOST` 常等於 `AGENT_INVOICE_HOST`），結帳時證明聯列印與錢櫃
kick 幾乎同時發生，各自開一條全新 TCP 連線去搶同一台印表機唯一的連線槽，其中一個
就連線逾時（店員只能拿鑰匙開櫃）。以 `(host, port)` 為鍵的 `threading.Lock`
序列化：同一台印表機的操作一次只跑一個，不同印表機之間仍可平行——`write()` 由
`anyio.to_thread.run_sync` 從真正的 OS 執行緒呼叫，故用 `threading.Lock`
而非 `asyncio.Lock`（後者只在同一個事件迴圈內有效，跨執行緒無效）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol, cast

from escpos.exceptions import DeviceNotFoundError
from escpos.printer import Network

from agent.config import PrinterEndpoint
from agent.errors import DeviceOffline, DeviceTimeout
from agent.escpos_printer import SupportsWrite, open_drawer

_registry_guard = threading.Lock()
_device_locks: dict[tuple[str, int], threading.Lock] = {}


def _lock_for(host: str, port: int) -> threading.Lock:
    """回傳（必要時建立）某台實體印表機專屬的鎖，全行程共用、以 (host, port) 為鍵。"""
    key = (host, port)
    with _registry_guard:
        lock = _device_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _device_locks[key] = lock
        return lock


class _EscposNetwork(Protocol):
    """python-escpos `Network` 的最小介面（連線/送 raw/關閉），供測試注入假印表機。"""

    def open(self, raise_not_found: bool = ...) -> None: ...

    def _raw(self, msg: bytes) -> None: ...

    def close(self) -> None: ...


PrinterFactory = Callable[[str, int, float], _EscposNetwork]


def _default_printer_factory(host: str, port: int, timeout: float) -> _EscposNetwork:
    """預設以真機 escpos `Network` 連線（host/port/timeout 由設定帶入、不寫死）。"""
    # escpos 無型別 stub，Network(...) 對 mypy 為 Any；以 Protocol 收斂回明確型別。
    return cast(_EscposNetwork, Network(host=host, port=port, timeout=timeout))


class NetworkEscposWriter:
    """連到網路 ESC/POS 印表機（EPSON）的 `SupportsWrite` 轉接層（lazy 連線、邊界翻錯）。"""

    def __init__(
        self,
        endpoint: PrinterEndpoint,
        *,
        printer_factory: PrinterFactory = _default_printer_factory,
    ) -> None:
        self._endpoint = endpoint
        self._printer_factory = printer_factory

    def write(self, data: bytes) -> None:
        """連線→送出 ESC/POS 位元組→關閉；連線/逾時錯誤翻成 DeviceError，且必關閉。

        對同一台印表機（同 host:port）的操作以鎖排隊——見模組頂部說明。
        """
        with _lock_for(self._endpoint.host, self._endpoint.port):
            printer = self._printer_factory(
                self._endpoint.host, self._endpoint.port, self._endpoint.timeout
            )
            try:
                try:
                    printer.open()
                except DeviceNotFoundError as exc:
                    # 連不上（被拒/不可達/連線逾時，escpos 已包成 DeviceNotFoundError）→ 離線
                    raise DeviceOffline(
                        f"EPSON {self._endpoint.host}:{self._endpoint.port} 連線失敗：{exc}"
                    ) from exc
                try:
                    printer._raw(data)
                except TimeoutError as exc:  # 送出逾時（TimeoutError 為 OSError 子類，須先攔）
                    raise DeviceTimeout(f"EPSON {self._endpoint.host} 列印逾時：{exc}") from exc
                except OSError as exc:  # 連線中斷/broken pipe 等
                    raise DeviceOffline(f"EPSON {self._endpoint.host} 列印中斷：{exc}") from exc
            finally:
                printer.close()


class RealCashDrawer:
    """經 EPSON drawer port 踢開錢櫃：kick 指令走同一台 EPSON 網路連線。"""

    def __init__(self, writer: SupportsWrite) -> None:
        self._writer = writer

    def open(self) -> None:
        """送出錢櫃 kick 指令（ESC p）；連線/逾時錯誤由 writer 邊界翻成 DeviceError。"""
        open_drawer(self._writer)
