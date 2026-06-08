"""真機裝置狀態驅動（A 級，網路 TCP 探測）。

`RealStatusProvider` 實作 `DeviceStatusProvider` Protocol。**兩台裝置皆為網路連線**
（Brother QL-810W、EPSON TM-T82III 均走 Ethernet/Wi-Fi），A 級統一以 **TCP 9100
連線探測 + 心跳**判定在線/離線（連得上＝在線；不依賴 ESC/POS DLE EOT 狀態回應，
避免「連得上但未回狀態」被誤判離線）。

B 級（缺紙/上蓋/印表機錯誤/錢櫃開關偵測）**產品裁示不做**（ADR-011）：這類狀態機器
本身會以燈號表現、店員現場肉眼可見，面板不重複偵測；對應鍵一律標 `unsupported`，
不臆造、不當故障（ADR-010）。錢櫃「彈開指令」屬列印/drawer 功能（經 EPSON drawer
port 另行實作），與此狀態驅動無關。

誠實原則（ADR-010、使用者要求）：
- 連不上（連線被拒/逾時/主機不可達/DNS，皆 `OSError`）→ 合理離線，`online=False`、
  `probe_error=None`。
- 其他非預期例外（設定/程式錯誤）→ `online=False` 但 `probe_error` 如實記，
  **不可偽裝成單純離線**。

IP/port 由 `agent.config` 經建構引數注入，**程式碼不寫死任何 IP**。
`validated_on_hardware=False`（全部）——待實機接上再更改（T18）。
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.config import PrinterEndpoint, device_config_from_env
from agent.interfaces import DeviceKind, DeviceStatus

# B 級（產品裁示不做）一律列入 unsupported，前端顯示「不支援」而非「故障」（ADR-010/011）
_PRINTER_UNSUPPORTED = ["paper_out", "cover_open", "error"]
_DRAWER_UNSUPPORTED = ["drawer_open"]


@dataclass(frozen=True)
class _ProbeResult:
    """TCP 探測結果：在線、心跳時間、（非離線的）探測錯誤。"""

    online: bool
    last_seen: datetime | None
    probe_error: str | None


def _tcp_probe(endpoint: PrinterEndpoint) -> _ProbeResult:
    """TCP 連線探測一台網路裝置（A 級：連得上＝在線），兩台共用。

    - 連線成功 → `online=True`、`last_seen=now`、`probe_error=None`。
    - `OSError`（連線被拒/逾時/主機不可達/DNS 失敗）→ 合理離線，`probe_error=None`。
    - 其他非預期例外（設定/程式錯誤）→ `online=False` 但 `probe_error` 如實記，
      **不偽裝成單純離線**。
    """
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=endpoint.timeout):
            return _ProbeResult(online=True, last_seen=datetime.now(UTC), probe_error=None)
    except OSError:
        # 連線被拒/逾時/不通/DNS 失敗 → 合理離線（非錯誤）
        return _ProbeResult(online=False, last_seen=None, probe_error=None)
    except Exception as exc:  # 非預期例外（設定/程式錯誤）須如實標示、不可吞成離線
        return _ProbeResult(
            online=False,
            last_seen=None,
            probe_error=f"探測 {endpoint.host}:{endpoint.port} 失敗（設定/程式錯誤）：{exc}",
        )


class RealStatusProvider:
    """真機裝置狀態提供者（A 級：online/last_seen 心跳；兩台皆網路 TCP 探測）。

    Args:
        epson: EPSON TM-T82III 連線端點（IP/port/逾時，由設定注入）。
        brother: Brother QL-810W 連線端點；`None` 表示未列管 Brother（測 A 只接 EPSON），
            此時 `poll()` 只回 EPSON 收據機 + 依附錢櫃。
    """

    def __init__(
        self, *, epson: PrinterEndpoint, brother: PrinterEndpoint | None = None
    ) -> None:
        self._brother = brother
        self._epson = epson

    def _poll_brother(self) -> DeviceStatus:
        """TCP 探測 Brother QL-810W；B 級全標 unsupported（網路後端不讀狀態、產品不做）。"""
        assert self._brother is not None  # 僅在 Brother 已列管時由 poll() 呼叫
        result = _tcp_probe(self._brother)
        return DeviceStatus(
            id="brother-1",
            kind=DeviceKind.LABEL_PRINTER,
            model="Brother QL-810W",
            online=result.online,
            last_seen=result.last_seen,
            details={},
            unsupported=list(_PRINTER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=False,
            probe_error=result.probe_error,
        )

    def _poll_epson(self) -> DeviceStatus:
        """TCP 探測 EPSON TM-T82III；B 級全標 unsupported（產品裁示不做，ADR-011）。"""
        result = _tcp_probe(self._epson)
        return DeviceStatus(
            id="epson-1",
            kind=DeviceKind.RECEIPT_PRINTER,
            model="EPSON TM-T82III",
            online=result.online,
            last_seen=result.last_seen,
            details={},
            unsupported=list(_PRINTER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=False,
            probe_error=result.probe_error,
        )

    def _poll_cash_drawer(self, *, epson: DeviceStatus) -> DeviceStatus:
        """錢櫃狀態依附 EPSON（掛在其 drawer port）；開關偵測不做（標 unsupported）。

        EPSON 探測錯誤一併如實傳達，錢櫃不可獨自顯示成正常。
        """
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
            unsupported=list(_DRAWER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=False,
            probe_error=probe_error,
        )

    def poll(self) -> list[DeviceStatus]:
        """輪詢裝置。Brother 有列管時順序 Brother → EPSON → 錢櫃；未列管則只回 EPSON → 錢櫃。"""
        epson = self._poll_epson()
        drawer = self._poll_cash_drawer(epson=epson)
        if self._brother is None:
            return [epson, drawer]
        return [self._poll_brother(), epson, drawer]


def real_status_provider_from_env() -> RealStatusProvider:
    """由環境變數（裝置 IP/port/逾時）建立真機狀態提供者；IP 不寫死於程式碼。"""
    config = device_config_from_env()
    return RealStatusProvider(brother=config.brother, epson=config.epson)
