"""真機 EPSON 網路驅動單元測試（測 A wiring）——全程免實機。

注入假的 escpos Network（記錄 open/_raw/close、可設定丟例外），驗證：
- `NetworkEscposWriter` lazy 連線、送出 ESC/POS 位元組、必關閉，且把連線/逾時的
  OSError 在邊界翻成 `agent.errors` 的 DeviceError（離線→DeviceOffline、逾時→DeviceTimeout）。
- `RealCashDrawer` 經同一連線送 kick 指令、錯誤同樣翻成 DeviceError。
- `real_epson_devices_from_env` 組出「EPSON 真機收據+錢櫃、Brother 維持 Fake、狀態 EPSON-only」。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from escpos.exceptions import DeviceNotFoundError

from agent.config import PrinterEndpoint
from agent.devices import real_epson_devices_from_env
from agent.drivers.escpos_network import NetworkEscposWriter, RealCashDrawer
from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.drivers.status_real import RealStatusProvider
from agent.errors import DeviceOffline, DeviceTimeout
from agent.fakes import FakeLabelPrinter

_EP = PrinterEndpoint(host="10.0.0.5", port=9100, timeout=2.0)


class _FakeNetwork:
    """假的 escpos Network：記錄呼叫、可設定 open/_raw 丟出指定例外。"""

    def __init__(
        self, *, open_exc: Exception | None = None, raw_exc: Exception | None = None
    ) -> None:
        self.open_exc = open_exc
        self.raw_exc = raw_exc
        self.sent: list[bytes] = []
        self.opened = False
        self.closed = False
        self.endpoint: tuple[str, int, float] | None = None

    def open(self, raise_not_found: bool = True) -> None:
        if self.open_exc is not None:
            raise self.open_exc
        self.opened = True

    def _raw(self, msg: bytes) -> None:
        if self.raw_exc is not None:
            raise self.raw_exc
        self.sent.append(msg)

    def close(self) -> None:
        self.closed = True


def _factory_for(fake: _FakeNetwork) -> Callable[[str, int, float], _FakeNetwork]:
    def factory(host: str, port: int, timeout: float) -> _FakeNetwork:
        fake.endpoint = (host, port, timeout)
        return fake

    return factory


def test_writer_sends_bytes_and_always_closes() -> None:
    fake = _FakeNetwork()
    writer = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake))
    writer.write(b"hello")
    assert fake.sent == [b"hello"]
    assert fake.closed is True
    assert fake.endpoint == ("10.0.0.5", 9100, 2.0)  # 連線資訊來自 endpoint、未寫死


def test_writer_maps_connect_failure_to_device_offline() -> None:
    fake = _FakeNetwork(open_exc=DeviceNotFoundError("connection refused"))
    writer = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake))
    with pytest.raises(DeviceOffline):
        writer.write(b"x")
    assert fake.closed is True  # 失敗也要關閉


def test_writer_maps_send_timeout_to_device_timeout() -> None:
    fake = _FakeNetwork(raw_exc=TimeoutError("timed out"))
    writer = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake))
    with pytest.raises(DeviceTimeout):
        writer.write(b"x")
    assert fake.closed is True


def test_writer_maps_broken_pipe_to_device_offline() -> None:
    fake = _FakeNetwork(raw_exc=BrokenPipeError("broken pipe"))
    writer = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake))
    with pytest.raises(DeviceOffline):
        writer.write(b"x")
    assert fake.closed is True


def test_real_cash_drawer_kicks_via_writer() -> None:
    fake = _FakeNetwork()
    writer = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake))
    RealCashDrawer(writer).open()
    assert len(fake.sent) == 1
    assert fake.sent[0].startswith(b"\x1bp")  # ESC p：錢櫃 kick 指令


def test_real_cash_drawer_offline_maps_device_error() -> None:
    fake = _FakeNetwork(open_exc=DeviceNotFoundError("refused"))
    writer = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake))
    with pytest.raises(DeviceOffline):
        RealCashDrawer(writer).open()


def test_real_epson_devices_builder_wires_epson_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """只需 AGENT_EPSON_HOST；receipt+drawer=真機、label=Fake、狀態 EPSON-only（無 Brother）。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)  # 不接 Brother、也不該必填
    devices = real_epson_devices_from_env()
    assert isinstance(devices.receipt_printer, EscposReceiptPrinter)
    assert isinstance(devices.cash_drawer, RealCashDrawer)
    assert isinstance(devices.label_printer, FakeLabelPrinter)  # Brother 維持 Fake
    assert isinstance(devices.status_provider, RealStatusProvider)
    # 狀態 EPSON-only：未列管 Brother（不真的連線、只看結構）
    assert devices.status_provider._brother is None
    assert devices.status_provider._epson.host == "192.168.0.42"
