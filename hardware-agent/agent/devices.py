"""裝置注入容器（Wave 2.0 骨架）。

`AgentDevices` 把四種裝置介面的具體實作綁成一包，注入 `create_app`。預設全用
Fake，實機上線時改注入真機驅動（T15/T16/T18），**上層路由零改動**。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.config import (
    brother_endpoint_from_env,
    drawer_endpoint_from_env,
    epson_endpoint_from_env,
    invoice_endpoint_from_env,
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
    simulated: bool = False
    """這組是不是「假裝列印」的裝置。

    假裝模式收到列印一樣回成功、紙卻不會出來。**列印回應要帶著這個事實**，前端才能
    對店員直說「這次沒有真的列印」——否則店員會拿著空手以為印好了。
    """
    invoice_printer: ReceiptPrinter | None = None
    """電子發票**專屬**印表機（ADR-018，選配）。

    `None`＝沒接發票機，證明聯印到收據機（單機店家的既有行為）。有接時這台**只印
    證明聯**（含 Amego 補印的原樣版面），收據/明細聯/收購憑證聯/出餐單一律不進來——
    它裝的可能是不同字型 ROM 的機器，也可能被店家刻意留給發票專用紙捲。
    """

    @property
    def kitchen_ticket_printer(self) -> ReceiptPrinter:
        """出餐單的實際目的地——**唯一的解析點**，呼叫端不得自行 or 一次。"""
        return self.kitchen_printer if self.kitchen_printer is not None else self.receipt_printer

    @property
    def einvoice_printer(self) -> ReceiptPrinter:
        """電子發票證明聯的實際目的地——**唯一的解析點**，呼叫端不得自行 or 一次。"""
        return self.invoice_printer if self.invoice_printer is not None else self.receipt_printer


def default_fake_devices() -> AgentDevices:
    """全 Fake 的預設組合（無實機開發與自動化測試用）。"""
    return AgentDevices(
        label_printer=FakeLabelPrinter(),
        receipt_printer=FakeReceiptPrinter(),
        cash_drawer=FakeCashDrawer(),
        status_provider=FakeStatusProvider(),
        simulated=True,
    )


def real_epson_devices_from_env() -> AgentDevices:
    """真機組合：EPSON 收據機 + 錢櫃必接；Brother 標籤機選配（T18）。

    - `receipt_printer`：`EscposReceiptPrinter` 包 `NetworkEscposWriter`（lazy 連 EPSON），
      中文編碼取自該端點（Big5 機/GB18030 機不同，見 ADR-018）。
    - `invoice_printer`：`AGENT_INVOICE_HOST` 有設 → 發票專屬機；未設 → `None`（證明聯
      印回收據機）。
    - `cash_drawer`：`RealCashDrawer` 經**錢櫃所接那台**的連線送 kick（`AGENT_DRAWER_HOST`
      未設即收據機；本店實機錢櫃插在發票機那台）。
    - `label_printer`：`AGENT_BROTHER_HOST` 有設 → `BrotherLabelPrinter`（brother_ql 光柵、
      網路）；未設 → `FakeLabelPrinter`（不列管）。
    - `status_provider`：探測 EPSON（+依附錢櫃）；Brother 有設一併列管。

    連線資訊（IP/port/逾時）一律由環境變數提供，程式碼不寫死。
    """
    epson = epson_endpoint_from_env()
    brother = brother_endpoint_from_env()
    kitchen = kitchen_endpoint_from_env()
    invoice = invoice_endpoint_from_env()
    drawer = drawer_endpoint_from_env()
    label_printer: LabelPrinter = (
        BrotherLabelPrinter(brother, font_path=label_font_path_from_env())
        if brother is not None
        else FakeLabelPrinter()
    )
    return AgentDevices(
        label_printer=label_printer,
        receipt_printer=EscposReceiptPrinter(NetworkEscposWriter(epson), encoding=epson.encoding),
        cash_drawer=RealCashDrawer(NetworkEscposWriter(drawer)),
        status_provider=RealStatusProvider(
            epson=epson, brother=brother, kitchen=kitchen, invoice=invoice, drawer=drawer
        ),
        kitchen_printer=(
            EscposReceiptPrinter(NetworkEscposWriter(kitchen), encoding=kitchen.encoding)
            if kitchen is not None
            else None
        ),
        invoice_printer=(
            EscposReceiptPrinter(NetworkEscposWriter(invoice), encoding=invoice.encoding)
            if invoice is not None
            else None
        ),
    )
