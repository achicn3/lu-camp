"""裝置注入容器（Wave 2.0 骨架）。

`AgentDevices` 把四種裝置介面的具體實作綁成一包，注入 `create_app`。預設全用
Fake，實機上線時改注入真機驅動（T15/T16/T18），**上層路由零改動**。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config import (
    brother_endpoint_from_env,
    epson_endpoint_from_env,
    kitchen_endpoint_from_env,
    label_font_path_from_env,
)
from agent.drivers.brother_label import BrotherLabelPrinter
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
    kitchen_printer: ReceiptPrinter | None = None
    """出餐單專用的第二台印表機（docs/35，選配）。

    `None`＝沒接第二台，出餐單印到收據機（既有行為）。放廚房/吧台的那台只印出餐單，
    不印客人的收據/明細聯/證明聯。
    """

    @property
    def kitchen_ticket_printer(self) -> ReceiptPrinter:
        """出餐單的實際目的地——**唯一的解析點**，呼叫端不得自行 or 一次。"""
        return self.kitchen_printer if self.kitchen_printer is not None else self.receipt_printer


def default_fake_devices() -> AgentDevices:
    """全 Fake 的預設組合（無實機開發與自動化測試用）。"""
    return AgentDevices(
        label_printer=FakeLabelPrinter(),
        receipt_printer=FakeReceiptPrinter(),
        cash_drawer=FakeCashDrawer(),
        status_provider=FakeStatusProvider(),
    )


def real_epson_devices_from_env() -> AgentDevices:
    """真機組合：EPSON 收據機 + 錢櫃必接；Brother 標籤機選配（T18）。

    - `receipt_printer`：`EscposReceiptPrinter` 包 `NetworkEscposWriter`（lazy 連 EPSON）。
    - `cash_drawer`：`RealCashDrawer` 經同一 EPSON 連線送 kick。
    - `label_printer`：`AGENT_BROTHER_HOST` 有設 → `BrotherLabelPrinter`（brother_ql 光柵、
      網路）；未設 → `FakeLabelPrinter`（不列管）。
    - `status_provider`：探測 EPSON（+依附錢櫃）；Brother 有設一併列管。

    連線資訊（IP/port/逾時）一律由環境變數提供，程式碼不寫死。
    """
    epson = epson_endpoint_from_env()
    brother = brother_endpoint_from_env()
    kitchen = kitchen_endpoint_from_env()
    writer = NetworkEscposWriter(epson)
    label_printer: LabelPrinter = (
        BrotherLabelPrinter(brother, font_path=label_font_path_from_env())
        if brother is not None
        else FakeLabelPrinter()
    )
    return AgentDevices(
        label_printer=label_printer,
        receipt_printer=EscposReceiptPrinter(writer),
        cash_drawer=RealCashDrawer(writer),
        status_provider=RealStatusProvider(epson=epson, brother=brother, kitchen=kitchen),
        kitchen_printer=(
            EscposReceiptPrinter(NetworkEscposWriter(kitchen)) if kitchen is not None else None
        ),
    )
