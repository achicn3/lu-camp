"""真機裝置狀態驅動（T16 A 級）。

`RealStatusProvider` 實作 `DeviceStatusProvider` Protocol。A 級只做連線/離線偵測：
- Brother QL-810W（Wi-Fi）：TCP 探測 port 9100；B 級標 unsupported。
- EPSON TM-T82III（USB）：用 python-escpos `Usb.open()` + `is_online()`。
- 錢櫃：依附 EPSON 在線狀態推定。

**不依賴真實硬體**：連線參數由建構引數傳入，所有 I/O 可在測試中 mock。
`validated_on_hardware=False`（全部）——等實機接上再更改（T18）。

ADR-010 原則：報不到的狀態標 unsupported，不臆造、不當故障。
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime

from escpos.exceptions import (  # type: ignore[import-untyped]
    DeviceNotFoundError,
    USBNotFoundError,
)
from escpos.printer import Usb  # type: ignore[import-untyped]
from usb.core import USBError  # type: ignore[import-untyped]

from agent.interfaces import DeviceKind, DeviceStatus


class RealStatusProvider:
    """真機裝置狀態提供者（A 級：online/last_seen 心跳）。

    Args:
        brother_host: Brother QL-810W 的 IP 或主機名。
        brother_port: TCP 探測 port（通常 9100）。
        brother_timeout: TCP 連線逾時秒數。
        epson_vendor_id: EPSON USB Vendor ID（0x04B8）。
        epson_product_id: EPSON USB Product ID（例如 0x0202）。
        epson_timeout_ms: EPSON USB 操作逾時（毫秒，預設 2000）；避免裝置不回應時
            is_online() 無限阻塞、拖垮被輪詢的 /devices/status。
    """

    def __init__(
        self,
        *,
        brother_host: str,
        brother_port: int = 9100,
        brother_timeout: float = 2.0,
        epson_vendor_id: int,
        epson_product_id: int,
        epson_timeout_ms: int = 2000,
    ) -> None:
        self._brother_host = brother_host
        self._brother_port = brother_port
        self._brother_timeout = brother_timeout
        self._epson_vendor_id = epson_vendor_id
        self._epson_product_id = epson_product_id
        self._epson_timeout_ms = epson_timeout_ms

    def _poll_brother(self) -> DeviceStatus:
        """TCP 探測 Brother QL-810W；B 級全標 unsupported（Wi-Fi 後端不支援讀狀態）。"""
        online = False
        last_seen: datetime | None = None
        try:
            sock = socket.create_connection(
                (self._brother_host, self._brother_port),
                timeout=self._brother_timeout,
            )
            sock.close()
            online = True
            last_seen = datetime.now(UTC)
        except OSError:
            pass

        return DeviceStatus(
            id="brother-1",
            kind=DeviceKind.LABEL_PRINTER,
            model="Brother QL-810W",
            online=online,
            last_seen=last_seen,
            details={},
            # Wi-Fi 後端無法查 B 級狀態（docs/15 §2）
            unsupported=["paper_out", "cover_open", "error"],
            driver="real",
            validated_on_hardware=False,
        )

    def _poll_epson(self) -> DeviceStatus:
        """USB 探測 EPSON TM-T82III。

        **如實區分兩種非在線情況，不把後者偽裝成單純離線**（ADR-010、使用者要求）：
        - 裝置未連接/找不到（DeviceNotFoundError/USBNotFoundError）→ 合理離線，`probe_error=None`。
        - 驅動/套件/設定錯誤（pyusb 未裝→RuntimeError、無 libusb 後端→NoBackendError、其他非預期）
          → `online=False` 但 `probe_error` 如實記錄，讓面板顯示錯誤、不誤導店員「只是離線」。
        """
        online = False
        last_seen: datetime | None = None
        probe_error: str | None = None
        printer = None
        try:
            # 設有限 USB timeout（python-escpos 預設 timeout=0 表「不逾時」，
            # 裝置在線但不回應即時狀態時 is_online() 會無限阻塞，拖垮被輪詢的 /devices/status）
            printer = Usb(
                idVendor=self._epson_vendor_id,
                idProduct=self._epson_product_id,
                timeout=self._epson_timeout_ms,
            )
            printer.open()
            online = bool(printer.is_online())
            if online:
                last_seen = datetime.now(UTC)
        except USBNotFoundError:
            # 裝置未連接/找不到 → 合理離線（非錯誤）
            online = False
        except DeviceNotFoundError as exc:
            # escpos 把「找不到裝置」與「USB 存取/權限/設定錯誤」都包成 DeviceNotFoundError；
            # 以被包的原始例外（__context__）區分：USBError → 真實權限/驅動/設定錯誤，須如實
            # 標示（udev/libusb 設定不對時最關鍵），不可偽裝成單純離線；否則才算裝置不在的離線。
            online = False
            if isinstance(exc.__context__, USBError):
                probe_error = f"EPSON USB 存取錯誤（權限/驅動/設定）：{exc}"
        except Exception as exc:
            # pyusb 未裝（RuntimeError）、無 libusb 後端（NoBackendError）或其他非預期錯誤
            # → 驅動/套件/設定錯誤，如實標示，不可偽裝成單純離線
            online = False
            probe_error = f"EPSON USB 探測失敗（驅動/套件/設定）：{exc}"
        finally:
            if printer is not None:
                try:
                    printer.close()
                except Exception:
                    # 關閉失敗不影響已判定狀態；無法把離線/錯誤偽裝成在線，故僅忽略
                    pass

        return DeviceStatus(
            id="epson-1",
            kind=DeviceKind.RECEIPT_PRINTER,
            model="EPSON TM-T82iii",
            online=online,
            last_seen=last_seen,
            details={},
            unsupported=[],
            driver="real",
            validated_on_hardware=False,
            probe_error=probe_error,
        )

    def _poll_cash_drawer(self, *, epson: DeviceStatus) -> DeviceStatus:
        """錢櫃狀態依附 EPSON（掛在 drawer port）；EPSON 探測錯誤一併如實傳達。"""
        last_seen = datetime.now(UTC) if epson.online else None
        probe_error = (
            f"依附 EPSON，EPSON 探測錯誤：{epson.probe_error}" if epson.probe_error else None
        )
        return DeviceStatus(
            id="drawer-1",
            kind=DeviceKind.CASH_DRAWER,
            model="EPSON drawer port",
            online=epson.online,
            last_seen=last_seen,
            details={},
            unsupported=[],
            driver="real",
            validated_on_hardware=False,
            probe_error=probe_error,
        )

    def poll(self) -> list[DeviceStatus]:
        """輪詢所有裝置；回傳順序：Brother → EPSON → 錢櫃。"""
        brother = self._poll_brother()
        epson = self._poll_epson()
        drawer = self._poll_cash_drawer(epson=epson)
        return [brother, epson, drawer]
