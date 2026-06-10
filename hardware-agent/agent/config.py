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


def einvoice_aes_key_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """讀電子發票 QR 加密驗證之 AES 金鑰（`AGENT_EINVOICE_AES_KEY`，32 位 hex）。

    選填：未設回 `None`（列印證明聯時才會要求）；有設但非 32 位 hex（AES-128 金鑰
    16 bytes）即報設定錯誤，避免帶錯金鑰印出無法驗證的發票。金鑰不入 repo。
    """
    resolved = os.environ if env is None else env
    key = resolved.get("AGENT_EINVOICE_AES_KEY", "").strip()
    if not key:
        return None
    try:
        valid = len(bytes.fromhex(key)) == 16
    except ValueError:
        valid = False
    if not valid:
        raise MissingDeviceConfigError(
            "AGENT_EINVOICE_AES_KEY 須為 32 位十六進位字串（AES-128 金鑰 16 bytes）。"
        )
    return key


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
