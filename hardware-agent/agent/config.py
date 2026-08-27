"""硬體代理裝置連線設定（IP/port/逾時，由環境變數外部化）。

兩台裝置（Brother QL-810W 標籤機、EPSON TM-T82III 收據機）**皆為網路連線**，
IP 一律由環境變數提供、**程式碼內不寫死任何 IP**（CLAUDE.md：絕不可 hardcode 裝置 IP）。
預設只給 port（9100，RAW/JetDirect raw print port）與探測逾時；**host 必填**，未設即
丟 `MissingDeviceConfigError`，避免落到臆造的 IP 上。

建議：在路由器以 DHCP 依 MAC 綁定固定 IP，IP 變更時只需改本設定（env / `.env`）一處。
見 `hardware-agent/.env.example`、docs/15、ADR-011。
"""

from __future__ import annotations

import codecs
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PORT = 9100  # RAW/JetDirect raw print port（兩台網路印表機通用）
_DEFAULT_PROBE_TIMEOUT = 2.0  # 秒；A 級 TCP 探測逾時，避免輪詢卡住 /devices/status
# 中文字型編碼預設 Big5：本店第一台 TM-T82III（S/N X7BJ020997）字型 ROM 為 TAIWAN BIG-5，
# 舊有單機部署一律如此。第二台起若字型 ROM 不同（實機 X7B4090696 為 CHINA GB18030），
# **必須**於該台的 env 指定，否則印出來整捲亂碼。見 ADR-018。
DEFAULT_ENCODING = "big5"


class MissingDeviceConfigError(Exception):
    """裝置設定缺漏或不可用（host 未提供、編碼名無效、字型檔不存在…）。

    不可寫死 IP，須由環境變數提供；設定有問題一律明確報錯，
    **不得默默退回預設值**（那會等到實機印出一整捲亂碼才被發現）。
    """


@dataclass(frozen=True)
class PrinterEndpoint:
    """單台網路裝置的連線端點（IP/port/探測逾時/中文編碼）。

    `encoding` 為送中文給**這一台**時用的字元編碼——同型號不同字型 ROM 的機器
    （Big5 機 vs GB18030 機）不能共用一個全域常數，故隨端點一起帶。
    """

    host: str
    port: int = _DEFAULT_PORT
    timeout: float = _DEFAULT_PROBE_TIMEOUT
    encoding: str = DEFAULT_ENCODING


@dataclass(frozen=True)
class DeviceConfig:
    """兩台網路裝置的連線設定。"""

    brother: PrinterEndpoint
    epson: PrinterEndpoint


def _encoding(env: Mapping[str, str], key: str) -> str:
    """讀某台印表機的中文編碼（未設 → Big5）；大小寫/空白正規化，無效編碼即報錯。

    打錯編碼名**不退回預設**：字型 ROM 對不上時印出的是整捲亂碼，等到那時才發現，
    紙與客人的時間都已經花掉了。
    """
    name = env.get(key, "").strip().lower()
    if not name:
        return DEFAULT_ENCODING
    try:
        codecs.lookup(name)
    except LookupError as exc:
        raise MissingDeviceConfigError(
            f"環境變數 {key} 指定的編碼「{name}」不是有效的字元編碼"
            f"（本店實機用 big5 或 gbk，見 ADR-018）。"
        ) from exc
    return name


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
        ),  # 標籤機走光柵（自帶字型），不吃 encoding
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


def kitchen_endpoint_from_env(env: Mapping[str, str] | None = None) -> PrinterEndpoint | None:
    """讀出餐單印表機連線端點（**選配**：第二台 EPSON，通常放廚房/吧台；docs/35）。

    `AGENT_KITCHEN_HOST` 未設/空白 → 回 `None`，出餐單即印到收據機（既有行為，
    在買到第二台之前不得因此壞掉）。有設即回端點；選填 `AGENT_KITCHEN_PORT`
    （預設 9100）、`AGENT_KITCHEN_ENCODING`（預設 big5）、
    `AGENT_DEVICE_PROBE_TIMEOUT`（預設 2.0 秒，與其他裝置共用）。
    IP 一律由環境變數提供、程式碼不寫死。
    """
    resolved = os.environ if env is None else env
    host = resolved.get("AGENT_KITCHEN_HOST", "").strip()
    if not host:
        return None
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return PrinterEndpoint(
        host=host,
        port=int(resolved.get("AGENT_KITCHEN_PORT", str(_DEFAULT_PORT))),
        timeout=timeout,
        encoding=_encoding(resolved, "AGENT_KITCHEN_ENCODING"),
    )


def invoice_endpoint_from_env(env: Mapping[str, str] | None = None) -> PrinterEndpoint | None:
    """讀發票專屬機連線端點（**選配**：第二台 EPSON，只印電子發票證明聯；ADR-018）。

    `AGENT_INVOICE_HOST` 未設/空白 → 回 `None`，證明聯即印到收據機（單機店家的既有
    行為，不得因此壞掉）。有設即回端點；選填 `AGENT_INVOICE_PORT`（預設 9100）、
    `AGENT_INVOICE_ENCODING`（預設 big5）、`AGENT_DEVICE_PROBE_TIMEOUT`（預設 2.0 秒）。
    IP 一律由環境變數提供、程式碼不寫死。
    """
    resolved = os.environ if env is None else env
    host = resolved.get("AGENT_INVOICE_HOST", "").strip()
    if not host:
        return None
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return PrinterEndpoint(
        host=host,
        port=int(resolved.get("AGENT_INVOICE_PORT", str(_DEFAULT_PORT))),
        timeout=timeout,
        encoding=_encoding(resolved, "AGENT_INVOICE_ENCODING"),
    )


def drawer_endpoint_from_env(env: Mapping[str, str] | None = None) -> PrinterEndpoint:
    """讀錢櫃所接印表機的連線端點（錢櫃掛在某台 EPSON 的 drawer port 上）。

    **回具體端點而非 `None`**：錢櫃一定接在某一台上，解析只在此處做一次——裝置組裝與
    狀態探測兩邊各自 `or` 一次遲早會漂移。`AGENT_DRAWER_HOST` 未設 → 沿用收據機
    （現行單機行為）；有設即用它（本店實機：錢櫃的線插在發票機那台，非收據機）。
    選填 `AGENT_DRAWER_PORT`（預設 9100）。

    錢櫃只送 kick 指令、不出紙，故把它指到「發票專屬機」不違反該機的專屬定位。
    """
    resolved = os.environ if env is None else env
    host = resolved.get("AGENT_DRAWER_HOST", "").strip()
    if not host:
        return epson_endpoint_from_env(resolved)
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return PrinterEndpoint(
        host=host,
        port=int(resolved.get("AGENT_DRAWER_PORT", str(_DEFAULT_PORT))),
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
    `AGENT_EPSON_PORT`（預設 9100）、`AGENT_EPSON_ENCODING`（預設 big5；本店這台字型 ROM
    為 GB18030，設 gbk）、`AGENT_DEVICE_PROBE_TIMEOUT`（預設 2.0 秒，連線/送出共用，
    避免用 escpos 預設 60 秒）。IP 一律由環境變數提供、程式碼不寫死。
    """
    resolved = os.environ if env is None else env
    timeout = float(resolved.get("AGENT_DEVICE_PROBE_TIMEOUT", str(_DEFAULT_PROBE_TIMEOUT)))
    return PrinterEndpoint(
        host=_require_host(resolved, "AGENT_EPSON_HOST"),
        port=int(resolved.get("AGENT_EPSON_PORT", str(_DEFAULT_PORT))),
        timeout=timeout,
        encoding=_encoding(resolved, "AGENT_EPSON_ENCODING"),
    )
