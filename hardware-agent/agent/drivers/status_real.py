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
`validated_on_hardware=True`：A 級 TCP 探測已於實機驗證（EPSON 在線/離線 2026-06-08~10、
Brother 在線 2026-06-11，T18／docs/15 §5）。
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, datetime

from agent.config import (
    PrinterEndpoint,
    device_config_from_env,
    drawer_endpoint_from_env,
    invoice_endpoint_from_env,
    kitchen_endpoint_from_env,
)
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
        kitchen: 出餐單印表機（第二台 EPSON，通常放廚房/吧台；docs/35）連線端點；
            `None` 表示未接第二台（出餐單印到櫃檯那台），不列管。
        invoice: 電子發票專屬機（ADR-018）連線端點；`None` 表示未接（證明聯印到收據機），
            不列管。
        drawer: 錢櫃所接那台印表機的連線端點（錢櫃掛在其 drawer port）。未給即沿用
            `epson`——但**正式路徑一律由 `agent.config.drawer_endpoint_from_env` 解析**，
            此預設只服務既有測試與單機組裝。
    """

    def __init__(
        self,
        *,
        epson: PrinterEndpoint,
        brother: PrinterEndpoint | None = None,
        kitchen: PrinterEndpoint | None = None,
        invoice: PrinterEndpoint | None = None,
        drawer: PrinterEndpoint | None = None,
    ) -> None:
        self._brother = brother
        self._epson = epson
        self._kitchen = kitchen
        self._invoice = invoice
        self._drawer = drawer if drawer is not None else epson

    def _poll_brother(self, result: _ProbeResult) -> DeviceStatus:
        """Brother QL-810W 狀態；B 級全標 unsupported（網路後端不讀狀態、產品不做）。"""
        return DeviceStatus(
            id="brother-1",
            kind=DeviceKind.LABEL_PRINTER,
            model="Brother QL-810W",
            online=result.online,
            last_seen=result.last_seen,
            details={},
            unsupported=list(_PRINTER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=True,
            probe_error=result.probe_error,
        )

    def _poll_epson(self, result: _ProbeResult) -> DeviceStatus:
        """EPSON TM-T82III（收據機）狀態；B 級全標 unsupported（產品裁示不做，ADR-011）。"""
        return DeviceStatus(
            id="epson-1",
            kind=DeviceKind.RECEIPT_PRINTER,
            model="EPSON TM-T82III",
            online=result.online,
            last_seen=result.last_seen,
            details={},
            unsupported=list(_PRINTER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=True,
            probe_error=result.probe_error,
        )

    def _poll_kitchen(self, result: _ProbeResult) -> DeviceStatus:
        """出餐機（第二台 EPSON）狀態。**必須列管**：它多半在廚房，沒人會看到它離線。"""
        return DeviceStatus(
            id="kitchen-1",
            kind=DeviceKind.RECEIPT_PRINTER,
            model="EPSON TM-T82III（出餐單）",
            online=result.online,
            last_seen=result.last_seen,
            details={},
            unsupported=list(_PRINTER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=False,
            probe_error=result.probe_error,
        )

    def _poll_invoice(self, result: _ProbeResult) -> DeviceStatus:
        """發票專屬機狀態（ADR-018）。**必須列管**：它離線時發票印不出來，
        而店員未必看得到那一台（可能擺在櫃檯另一側或後方）。"""
        return DeviceStatus(
            id="invoice-1",
            kind=DeviceKind.RECEIPT_PRINTER,
            model="EPSON TM-T82III（電子發票）",
            online=result.online,
            last_seen=result.last_seen,
            details={},
            unsupported=list(_PRINTER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=True,
            probe_error=result.probe_error,
        )

    def _poll_cash_drawer(self, result: _ProbeResult) -> DeviceStatus:
        """錢櫃狀態依附**它實際插的那台**（drawer port）；開關偵測不做（標 unsupported）。

        不可一律依附收據機：本店錢櫃的線插在發票機那台，收據機在線不代表踢得動錢櫃。
        探測錯誤一併如實傳達，錢櫃不可獨自顯示成正常。
        """
        last_seen = result.last_seen
        probe_error = (
            f"依附 {self._drawer.host}，該台探測錯誤：{result.probe_error}"
            if result.probe_error
            else None
        )
        return DeviceStatus(
            id="drawer-1",
            kind=DeviceKind.CASH_DRAWER,
            model="EPSON drawer port",
            online=result.online,
            last_seen=last_seen,
            details={},
            unsupported=list(_DRAWER_UNSUPPORTED),
            driver="real",
            validated_on_hardware=True,
            probe_error=probe_error,
        )

    def poll(self) -> list[DeviceStatus]:
        """輪詢裝置：Brother（有列管才有）→ EPSON → 發票機/出餐機（有接才有）→ 錢櫃。

        **同一台只探測一次**：錢櫃多半與某台印表機共用連線（未設 `AGENT_DRAWER_HOST`
        即收據機，本店則是發票機），為它再開一條 TCP 只是讓每次輪詢多等一個逾時。
        """
        probed: dict[tuple[str, int], _ProbeResult] = {}

        def probe(endpoint: PrinterEndpoint) -> _ProbeResult:
            key = (endpoint.host, endpoint.port)
            if key not in probed:
                probed[key] = _tcp_probe(endpoint)
            return probed[key]

        statuses = [] if self._brother is None else [self._poll_brother(probe(self._brother))]
        statuses.append(self._poll_epson(probe(self._epson)))
        if self._invoice is not None:
            statuses.append(self._poll_invoice(probe(self._invoice)))
        if self._kitchen is not None:
            statuses.append(self._poll_kitchen(probe(self._kitchen)))
        statuses.append(self._poll_cash_drawer(probe(self._drawer)))
        return statuses


def real_status_provider_from_env() -> RealStatusProvider:
    """由環境變數（裝置 IP/port/逾時）建立真機狀態提供者；IP 不寫死於程式碼。"""
    config = device_config_from_env()
    return RealStatusProvider(
        brother=config.brother,
        epson=config.epson,
        kitchen=kitchen_endpoint_from_env(),
        invoice=invoice_endpoint_from_env(),
        drawer=drawer_endpoint_from_env(),
    )
