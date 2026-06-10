"""裝置注入容器（Wave 2.0 骨架）。

`AgentDevices` 把四種裝置介面的具體實作綁成一包，注入 `create_app`。預設全用
Fake，實機上線時改注入真機驅動（T15/T16/T18），**上層路由零改動**。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config import einvoice_aes_key_from_env, epson_endpoint_from_env
from agent.drivers.escpos_network import NetworkEscposWriter, RealCashDrawer
from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.drivers.status_real import RealStatusProvider
from agent.fakes import (
    FakeCashDrawer,
    FakeLabelPrinter,
    FakeReceiptPrinter,
    FakeStatusProvider,
)
from agent.interfaces import (
    CashDrawer,
    DeviceStatusProvider,
    LabelPrinter,
    ReceiptPrinter,
)


@dataclass(frozen=True)
class AgentDevices:
    """注入給 app 的裝置實作組合（介面型別，與具體實作解耦）。"""

    label_printer: LabelPrinter
    receipt_printer: ReceiptPrinter
    cash_drawer: CashDrawer
    status_provider: DeviceStatusProvider


def default_fake_devices() -> AgentDevices:
    """全 Fake 的預設組合（無實機開發與自動化測試用）。"""
    return AgentDevices(
        label_printer=FakeLabelPrinter(),
        receipt_printer=FakeReceiptPrinter(),
        cash_drawer=FakeCashDrawer(),
        status_provider=FakeStatusProvider(),
    )


def real_epson_devices_from_env() -> AgentDevices:
    """測 A 真機組合：EPSON 收據機 + 錢櫃走網路真機；Brother 標籤機維持 Fake（未接）。

    - `receipt_printer`：`EscposReceiptPrinter` 包 `NetworkEscposWriter`（lazy 連 EPSON）。
    - `cash_drawer`：`RealCashDrawer` 經同一 EPSON 連線送 kick。
    - `label_printer`：`FakeLabelPrinter`（Brother 之後再接，本次不列管）。
    - `status_provider`：`RealStatusProvider` 只探測 EPSON（+依附錢櫃），不列管 Brother。

    EPSON 連線資訊（IP/port/逾時）由環境變數提供（`AGENT_EPSON_HOST` 等），程式碼不寫死。
    """
    epson = epson_endpoint_from_env()
    writer = NetworkEscposWriter(epson)
    return AgentDevices(
        label_printer=FakeLabelPrinter(),
        receipt_printer=EscposReceiptPrinter(writer, einvoice_aes_key=einvoice_aes_key_from_env()),
        cash_drawer=RealCashDrawer(writer),
        status_provider=RealStatusProvider(epson=epson),
    )
