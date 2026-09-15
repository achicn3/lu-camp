"""裝置注入容器（Wave 2.0 骨架）。

`AgentDevices` 把四種裝置介面的具體實作綁成一包，注入 `create_app`。預設全用
Fake，實機上線時改注入真機驅動（T15/T16/T18），**上層路由零改動**。
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass

from agent.config import (
    PrinterEndpoint,
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

# 「按了不會有東西出來」的實作。判定假裝模式時比對這份清單，而不是相信某個旗標。
_FAKE_IMPLEMENTATIONS = (
    FakeLabelPrinter,
    FakeReceiptPrinter,
    FakeCashDrawer,
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
    invoice_printer: ReceiptPrinter | None = None
    """電子發票**專屬**印表機（ADR-018，選配）。

    `None`＝沒接發票機，證明聯印到收據機（單機店家的既有行為）。有接時這台**只印
    證明聯**（含 Amego 補印的原樣版面），收據/明細聯/收購憑證聯/出餐單一律不進來——
    它裝的可能是不同字型 ROM 的機器，也可能被店家刻意留給發票專用紙捲。
    """

    @property
    def simulated_devices(self) -> tuple[str, ...]:
        """實際上沒有接到真機、按了也不會有東西出來的裝置（給人看的名稱）。

        **由注入的物件推得，不由工廠宣告**：`real` 模式下沒設 host 的裝置會悄悄退回
        Fake（例如標籤機選配），若靠一個整組旗標來記，那種混合配置就會謊報「都是真的」，
        於是「按了列印標籤卻什麼都沒出來」完全無跡可循。改成看物件，日後多接一台也
        不必記得同步任何旗標。
        """
        return tuple(
            name
            for name, device in (
                ("標籤機", self.label_printer),
                ("收據機", self.receipt_printer),
                ("出餐機", self.kitchen_printer),
                ("發票機", self.invoice_printer),
                ("錢櫃", self.cash_drawer),
            )
            # None＝沒接第二台、印到收據機（既有設計），不是「假裝」，故不點名。
            if device is not None and isinstance(device, _FAKE_IMPLEMENTATIONS)
        )

    def simulated_for(self, binding: str) -> bool:
        """某個端點用的那台是不是假的。

        逐端點認定而非整組認定：標籤機沒接，不代表收據也印不出來——把兩者混為一談，
        警告就會過度氾濫而被無視。
        """
        device = {
            "label": self.label_printer,
            "receipt": self.receipt_printer,
            "kitchen": self.kitchen_ticket_printer,
            "einvoice": self.einvoice_printer,
            "drawer": self.cash_drawer,
        }[binding]
        return isinstance(device, _FAKE_IMPLEMENTATIONS)

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
    )


_DEFAULT_PRINT_TIMEOUT = 8.0
"""真正列印/開櫃連線逾時（秒）預設值——與背景健康探測（`AGENT_DEVICE_PROBE_TIMEOUT`，
預設 2.0 秒）刻意分開：探測要跑得快，但真實操作若遇上印表機正忙著印上一張單子
（結帳時證明聯列印與錢櫃 kick 常幾乎同時發生，見 2026-09-15 實測），2 秒太容易誤判
離線。真正的併發防護是 `escpos_network._lock_for` 的排隊鎖，這個較長的逾時只是
「鎖排到它時，印表機的 TCP 監聽是否已釋放」這道更窄窗口的餘裕，非主要防線。
"""


def _with_print_timeout(endpoint: PrinterEndpoint, env: Mapping[str, str]) -> PrinterEndpoint:
    """回傳同一端點的副本，但逾時改用 `AGENT_PRINT_TIMEOUT`（給真正列印/開櫃用，
    不影響傳給 `RealStatusProvider` 的原始端點——背景探測仍用探測逾時）。"""
    print_timeout = float(env.get("AGENT_PRINT_TIMEOUT", str(_DEFAULT_PRINT_TIMEOUT)))
    return dataclasses.replace(endpoint, timeout=print_timeout)


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
    - `status_provider`：探測 EPSON（+依附錢櫃）；Brother 有設一併列管——用**探測用**逾時
      （`AGENT_DEVICE_PROBE_TIMEOUT`），與真正列印/開櫃用的逾時分開（見 `_with_print_timeout`）。

    連線資訊（IP/port/逾時）一律由環境變數提供，程式碼不寫死。
    """
    env = os.environ
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
    epson_w = _with_print_timeout(epson, env)
    drawer_w = _with_print_timeout(drawer, env)
    kitchen_w = _with_print_timeout(kitchen, env) if kitchen is not None else None
    invoice_w = _with_print_timeout(invoice, env) if invoice is not None else None
    return AgentDevices(
        label_printer=label_printer,
        receipt_printer=EscposReceiptPrinter(
            NetworkEscposWriter(epson_w), encoding=epson.encoding
        ),
        cash_drawer=RealCashDrawer(NetworkEscposWriter(drawer_w)),
        status_provider=RealStatusProvider(
            epson=epson, brother=brother, kitchen=kitchen, invoice=invoice, drawer=drawer
        ),
        kitchen_printer=(
            EscposReceiptPrinter(NetworkEscposWriter(kitchen_w), encoding=kitchen.encoding)
            if kitchen is not None and kitchen_w is not None
            else None
        ),
        invoice_printer=(
            EscposReceiptPrinter(NetworkEscposWriter(invoice_w), encoding=invoice.encoding)
            if invoice_w is not None and invoice is not None
            else None
        ),
    )
