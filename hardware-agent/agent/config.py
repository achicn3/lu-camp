"""硬體代理裝置連線設定（IP/port/逾時，由環境變數外部化）。

兩台裝置（Brother QL-810W 標籤機、EPSON TM-T82III 收據機）**皆為網路連線**，
IP 一律由環境變數提供、**程式碼內不寫死任何 IP**（CLAUDE.md：絕不可 hardcode 裝置 IP）。
預設只給 port（9100，RAW/JetDirect raw print port）與探測逾時；**host 必填**，未設即
丟 `MissingDeviceConfigError`，避免落到臆造的 IP 上。

建議：在路由器以 DHCP 依 MAC 綁定固定 IP，IP 變更時只需改本設定（env / `.env`）一處。
見 `hardware-agent/.env.example`、docs/15、ADR-011。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PORT = 9100  # RAW/JetDirect raw print port（兩台網路印表機通用）
_DEFAULT_PROBE_TIMEOUT = 2.0  # 秒；A 級 TCP 探測逾時，避免輪詢卡住 /devices/status


class MissingDeviceConfigError(Exception):
    """必填的裝置連線設定（host）未提供——不可寫死 IP，須由環境變數提供。"""


@dataclass(frozen=True)
class PrinterEndpoint:
    """單台網路裝置的連線端點（IP/port/探測逾時）。"""

    host: str
    port: int = _DEFAULT_PORT
    timeout: float = _DEFAULT_PROBE_TIMEOUT


@dataclass(frozen=True)
class DeviceConfig:
    """兩台網路裝置的連線設定。"""

    brother: PrinterEndpoint
    epson: PrinterEndpoint


def _require_host(env: Mapping[str, str], key: str) -> str:
    """讀必填 host；未設或空白即報錯（不臆造 IP）。"""
    host = env.get(key, "").strip()
    if not host:
        raise MissingDeviceConfigError(
            f"環境變數 {key} 未設定；裝置 IP 不可寫死於程式碼，請於 env/.env 提供"
            f"（見 hardware-agent/.env.example）。"
        )
    return host


def device_config_from_env(env: Mapping[str, str] | None = None) -> DeviceConfig:
    """由環境變數建立裝置連線設定。

    必填：`AGENT_BROTHER_HOST`、`AGENT_EPSON_HOST`（未設即丟 `MissingDeviceConfigError`）。
    選填（有預設）：`AGENT_BROTHER_PORT`/`AGENT_EPSON_PORT`（預設 9100）、
    `AGENT_DEVICE_PROBE_TIMEOUT`（預設 2.0 秒，兩台共用）。

    Args:
        env: 環境對應表；預設 `os.environ`（測試可注入固定字典）。
    """
    resolved = os.environ if env is None else env
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return DeviceConfig(
        brother=PrinterEndpoint(
            host=_require_host(resolved, "AGENT_BROTHER_HOST"),
            port=int(resolved.get("AGENT_BROTHER_PORT", str(_DEFAULT_PORT))),
            timeout=timeout,
        ),
        epson=epson_endpoint_from_env(resolved),
    )


def brother_endpoint_from_env(env: Mapping[str, str] | None = None) -> PrinterEndpoint | None:
    """讀 Brother QL-810W 連線端點（**選配**：T18 接真機才設）。

    `AGENT_BROTHER_HOST` 未設/空白 → 回 `None`（標籤機維持 Fake、狀態不列管 Brother），
    不報錯——與 EPSON（測 A 必接、host 必填）不同。有設即回端點；選填
    `AGENT_BROTHER_PORT`（預設 9100）、`AGENT_DEVICE_PROBE_TIMEOUT`（預設 2.0 秒）。
    IP 一律由環境變數提供、程式碼不寫死。
    """
    resolved = os.environ if env is None else env
    host = resolved.get("AGENT_BROTHER_HOST", "").strip()
    if not host:
        return None
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return PrinterEndpoint(
        host=host,
        port=int(resolved.get("AGENT_BROTHER_PORT", str(_DEFAULT_PORT))),
        timeout=timeout,
    )


# repo 內建標籤字型（Noto Sans TC，OFL 授權，見 assets/fonts/OFL.txt）：標籤品名為
# 繁體中文，部署主機不一定裝有 CJK 字型，故 repo 自帶、預設使用。
_BUNDLED_LABEL_FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansTC.ttf"


def label_font_path_from_env(env: Mapping[str, str] | None = None) -> str:
    """讀標籤字型路徑（`AGENT_LABEL_FONT`，選填；預設 repo 內建 Noto Sans TC）。

    有設但檔案不存在即報設定錯誤（不無聲退回預設，避免印出非預期字型）。
    """
    resolved = os.environ if env is None else env
    override = resolved.get("AGENT_LABEL_FONT", "").strip()
    if override:
        if not Path(override).is_file():
            raise MissingDeviceConfigError(f"AGENT_LABEL_FONT 指定的字型檔不存在：{override}")
        return override
    return str(_BUNDLED_LABEL_FONT)


def epson_endpoint_from_env(env: Mapping[str, str] | None = None) -> PrinterEndpoint:
    """只讀 EPSON 連線端點（測 A：只接 EPSON 收據機+錢櫃，**不要求** Brother host）。

    必填 `AGENT_EPSON_HOST`（未設即丟 `MissingDeviceConfigError`，不臆造 IP）；選填
    `AGENT_EPSON_PORT`（預設 9100）、`AGENT_DEVICE_PROBE_TIMEOUT`（預設 2.0 秒，連線/送出
    共用，避免用 escpos 預設 60 秒）。IP 一律由環境變數提供、程式碼不寫死。
    """
    resolved = os.environ if env is None else env
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return PrinterEndpoint(
        host=_require_host(resolved, "AGENT_EPSON_HOST"),
        port=int(resolved.get("AGENT_EPSON_PORT", str(_DEFAULT_PORT))),
        timeout=timeout,
    )
