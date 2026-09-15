"""真機 EPSON 網路驅動單元測試（測 A wiring）——全程免實機。

注入假的 escpos Network（記錄 open/_raw/close、可設定丟出指定例外），驗證：
- `NetworkEscposWriter` lazy 連線、送出 ESC/POS 位元組、必關閉，且把連線/逾時的
  OSError 在邊界翻成 `agent.errors` 的 DeviceError（離線→DeviceOffline、逾時→DeviceTimeout）。
- `RealCashDrawer` 經同一連線送 kick 指令、錯誤同樣翻成 DeviceError。
- **同一台實體印表機的操作必須排隊**（2026-09-15 實測踩過的真故障：錢櫃跟發票證明聯
  共用同一台 EPSON 的同一條 RJ45，結帳時兩個請求幾乎同時各開一條 TCP 連線，其中一個
  連線逾時、店員只能拿鑰匙開櫃）。`NetworkEscposWriter` 現在以 `(host, port)` 為鍵用
  `threading.Lock` 序列化——不同印表機之間仍可平行，同一台則不可同時有兩條連線。
- `real_epson_devices_from_env` 組出「EPSON 真機收據+錢櫃、Brother 維持 Fake、狀態 EPSON-only」，
  且真正列印/開櫃用的逾時（`AGENT_PRINT_TIMEOUT`）比背景健康探測用的逾時
  （`AGENT_DEVICE_PROBE_TIMEOUT`）更寬容——探測要跑得快，但真實操作不該因印表機
  正忙著印上一張單子就直接判離線。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from escpos.exceptions import DeviceNotFoundError

from agent.config import PrinterEndpoint
from agent.devices import real_epson_devices_from_env
from agent.drivers import escpos_network
from agent.drivers.brother_label import BrotherLabelPrinter
from agent.drivers.escpos_network import NetworkEscposWriter, RealCashDrawer
from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.drivers.status_real import RealStatusProvider
from agent.errors import DeviceOffline, DeviceTimeout
from agent.fakes import FakeLabelPrinter

_WAIT_TIMEOUT = 5.0

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


class _ConcurrencyTracker:
    """執行緒安全地記錄「同時間有幾個在跑」的峰值，用來斷言有沒有真的排隊/真的平行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def enter(self) -> None:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)

    def exit(self) -> None:
        with self._lock:
            self._active -= 1


class _TrackingFakeNetwork(_FakeNetwork):
    """從 open 到 close 記錄連線數；在連線內執行明確的同步協調。"""

    def __init__(self, tracker: _ConcurrencyTracker, on_write: Callable[[], None]) -> None:
        super().__init__()
        self._tracker = tracker
        self._on_write = on_write
        self.connected = threading.Event()

    def open(self, raise_not_found: bool = True) -> None:
        super().open(raise_not_found)
        self._tracker.enter()
        self.connected.set()

    def _raw(self, msg: bytes) -> None:
        self._on_write()
        super()._raw(msg)

    def close(self) -> None:
        super().close()
        self._tracker.exit()


def _write_in_thread(writer: NetworkEscposWriter, data: bytes, errors: list[Exception]) -> None:
    """把工作執行緒的例外帶回主執行緒斷言，避免只產生 pytest warning。"""
    try:
        writer.write(data)
    except Exception as exc:
        errors.append(exc)


def test_writer_serializes_concurrent_operations_to_same_host_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 host:port 的第二個 writer 必須在第一條連線關閉前等待同一把鎖。"""
    tracker = _ConcurrencyTracker()
    release_first = threading.Event()
    second_blocked = threading.Event()
    observed_locks: list[threading.Lock] = []
    original_lock_for = escpos_network._lock_for

    @contextmanager
    def observe_lock(host: str, port: int) -> Iterator[None]:
        # 使用產品真正的鎖；非阻塞取得失敗才通知主執行緒，證明已發生競爭。
        lock = original_lock_for(host, port)
        observed_locks.append(lock)
        if not lock.acquire(blocking=False):
            second_blocked.set()
            assert lock.acquire(timeout=_WAIT_TIMEOUT), "writer timed out waiting for lock"
        try:
            yield
        finally:
            lock.release()

    monkeypatch.setattr(escpos_network, "_lock_for", observe_lock)

    def hold_first_connection() -> None:
        assert release_first.wait(timeout=_WAIT_TIMEOUT), "first connection was not released"

    fake1 = _TrackingFakeNetwork(tracker, hold_first_connection)
    fake2 = _TrackingFakeNetwork(tracker, lambda: None)
    writer1 = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake1))
    writer2 = NetworkEscposWriter(_EP, printer_factory=_factory_for(fake2))
    errors: list[Exception] = []
    t1 = threading.Thread(
        target=_write_in_thread, args=(writer1, b"invoice-proof", errors), daemon=True
    )
    t2 = threading.Thread(
        target=_write_in_thread, args=(writer2, b"drawer-kick", errors), daemon=True
    )
    t1.start()
    try:
        assert fake1.connected.wait(timeout=_WAIT_TIMEOUT), "first writer did not connect"
        t2.start()
        assert second_blocked.wait(timeout=_WAIT_TIMEOUT), "second writer did not contend for lock"
        assert len(observed_locks) == 2
        assert observed_locks[0] is observed_locks[1]
        first_closed_while_second_waits = fake1.closed
        assert not first_closed_while_second_waits
        assert not fake2.connected.is_set()
        assert tracker.max_active == 1
    finally:
        release_first.set()
        t1.join(timeout=_WAIT_TIMEOUT)
        if t2.ident is not None:
            t2.join(timeout=_WAIT_TIMEOUT)

    assert not t1.is_alive() and not t2.is_alive()
    assert not errors, errors
    assert tracker.max_active == 1  # 從未同時有兩條連線在同一台印表機上
    assert fake1.sent == [b"invoice-proof"]
    assert fake2.sent == [b"drawer-kick"]
    assert fake1.closed and fake2.closed


def test_writer_does_not_serialize_operations_to_different_hosts() -> None:
    """不同 host 的兩條連線必須在 close 前於 Barrier 會合，證明確實同時連線。"""
    tracker = _ConcurrencyTracker()
    connected_together = threading.Barrier(2, timeout=_WAIT_TIMEOUT)

    def meet_inside_connection() -> None:
        connected_together.wait(timeout=_WAIT_TIMEOUT)

    ep_a = PrinterEndpoint(host="10.0.0.5", port=9100, timeout=2.0)
    ep_b = PrinterEndpoint(host="10.0.0.6", port=9100, timeout=2.0)
    fake_a = _TrackingFakeNetwork(tracker, meet_inside_connection)
    fake_b = _TrackingFakeNetwork(tracker, meet_inside_connection)
    writer_a = NetworkEscposWriter(ep_a, printer_factory=_factory_for(fake_a))
    writer_b = NetworkEscposWriter(ep_b, printer_factory=_factory_for(fake_b))
    errors: list[Exception] = []
    t1 = threading.Thread(target=_write_in_thread, args=(writer_a, b"a", errors), daemon=True)
    t2 = threading.Thread(target=_write_in_thread, args=(writer_b, b"b", errors), daemon=True)
    t1.start()
    t2.start()
    try:
        t1.join(timeout=_WAIT_TIMEOUT)
        t2.join(timeout=_WAIT_TIMEOUT)
    finally:
        connected_together.abort()
        t1.join(timeout=_WAIT_TIMEOUT)
        t2.join(timeout=_WAIT_TIMEOUT)

    assert not t1.is_alive() and not t2.is_alive()
    assert not errors, errors
    assert tracker.max_active == 2  # 兩台不同印表機真的同時在跑,不該互相卡隊
    assert fake_a.sent == [b"a"]
    assert fake_b.sent == [b"b"]
    assert fake_a.closed and fake_b.closed


def test_real_epson_devices_builder_wires_epson_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """只設 AGENT_EPSON_HOST；receipt+drawer=真機、label=Fake、狀態 EPSON-only（無 Brother）。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)  # 不接 Brother、也不該必填
    devices = real_epson_devices_from_env()
    assert isinstance(devices.receipt_printer, EscposReceiptPrinter)
    assert isinstance(devices.cash_drawer, RealCashDrawer)
    assert isinstance(devices.label_printer, FakeLabelPrinter)  # Brother 未設維持 Fake
    assert isinstance(devices.status_provider, RealStatusProvider)
    # 狀態 EPSON-only：未列管 Brother（不真的連線、只看結構）
    assert devices.status_provider._brother is None
    assert devices.status_provider._epson.host == "192.168.0.42"


def test_real_devices_builder_wires_brother_when_host_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGENT_BROTHER_HOST 有設 → 標籤機接真機 Brother 驅動、狀態列管 Brother（T18）。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.setenv("AGENT_BROTHER_HOST", "192.0.2.45")
    devices = real_epson_devices_from_env()
    assert isinstance(devices.label_printer, BrotherLabelPrinter)
    assert isinstance(devices.status_provider, RealStatusProvider)
    assert devices.status_provider._brother is not None
    assert devices.status_provider._brother.host == "192.0.2.45"


def test_real_devices_builder_wires_kitchen_printer_when_host_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGENT_KITCHEN_HOST 有設 → 出餐單走**獨立的第二台**，且狀態一併列管（docs/35）。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)
    monkeypatch.setenv("AGENT_KITCHEN_HOST", "192.0.2.60")
    devices = real_epson_devices_from_env()
    assert isinstance(devices.kitchen_printer, EscposReceiptPrinter)
    # 必須是**另一台**，不可是收據機本身（否則兩台設定等於沒生效）
    assert devices.kitchen_printer is not devices.receipt_printer
    assert devices.kitchen_ticket_printer is devices.kitchen_printer
    assert isinstance(devices.status_provider, RealStatusProvider)
    assert devices.status_provider._kitchen is not None
    assert devices.status_provider._kitchen.host == "192.0.2.60"


def test_real_devices_builder_falls_back_to_receipt_printer_without_kitchen_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒接第二台 → 出餐單印到收據機，且狀態頁**不得**憑空多出一台出餐機。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)
    monkeypatch.delenv("AGENT_KITCHEN_HOST", raising=False)
    devices = real_epson_devices_from_env()
    assert devices.kitchen_printer is None
    assert devices.kitchen_ticket_printer is devices.receipt_printer
    assert isinstance(devices.status_provider, RealStatusProvider)
    assert devices.status_provider._kitchen is None


def test_real_devices_use_longer_timeout_for_writes_than_for_background_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真正列印/開櫃（`NetworkEscposWriter`）用 `AGENT_PRINT_TIMEOUT`（預設較寬容）；
    背景健康探測（`RealStatusProvider`）仍用 `AGENT_DEVICE_PROBE_TIMEOUT`（預設較短、
    避免拖慢 /devices/status 輪詢）——兩者不可再共用同一個 2 秒值,那正是錢櫃 kick
    在印表機忙碌片刻時就被誤判逾時的原因之一。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)
    monkeypatch.setenv("AGENT_DEVICE_PROBE_TIMEOUT", "2.0")
    monkeypatch.setenv("AGENT_PRINT_TIMEOUT", "8.0")
    devices = real_epson_devices_from_env()
    assert isinstance(devices.receipt_printer, EscposReceiptPrinter)
    writer = devices.receipt_printer._writer
    assert isinstance(writer, NetworkEscposWriter)
    assert writer._endpoint.timeout == 8.0
    assert isinstance(devices.cash_drawer, RealCashDrawer)
    drawer_writer = devices.cash_drawer._writer
    assert isinstance(drawer_writer, NetworkEscposWriter)
    assert drawer_writer._endpoint.timeout == 8.0
    # 背景探測維持短逾時,不受 AGENT_PRINT_TIMEOUT 影響
    assert isinstance(devices.status_provider, RealStatusProvider)
    assert devices.status_provider._epson.timeout == 2.0


def test_real_devices_print_timeout_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """未設 `AGENT_PRINT_TIMEOUT` 時仍要有個比探測逾時寬容的預設值,不是退回 2 秒。"""
    monkeypatch.setenv("AGENT_EPSON_HOST", "192.168.0.42")
    monkeypatch.delenv("AGENT_BROTHER_HOST", raising=False)
    monkeypatch.delenv("AGENT_PRINT_TIMEOUT", raising=False)
    devices = real_epson_devices_from_env()
    assert isinstance(devices.receipt_printer, EscposReceiptPrinter)
    writer = devices.receipt_printer._writer
    assert isinstance(writer, NetworkEscposWriter)
    assert writer._endpoint.timeout > 2.0
