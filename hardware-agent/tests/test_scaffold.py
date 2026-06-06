"""Wave 2.0 骨架測試：介面一致性、Fake 失敗模擬、錯誤→HTTP 映射、注入切換。"""

import httpx
import pytest

from agent.devices import AgentDevices, default_fake_devices
from agent.errors import (
    CoverOpen,
    DeviceOffline,
    DeviceTimeout,
    DrawerNotConnected,
    PaperOut,
)
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
    SaleLinePayload,
    SalePayload,
    StoreHeader,
)
from agent.main import create_app

_SALE = SalePayload(
    id=1,
    store_id=1,
    subtotal="952",
    tax="48",
    total="1000",
    payment_method="CASH",
    invoice_status="NOT_ISSUED",
    created_at="2026-06-06T00:00:00Z",
    lines=[
        SaleLinePayload(
            line_type="CATALOG", description="帳篷", qty=1, unit_price="1000", line_total="1000"
        )
    ],
)
_HEADER = StoreHeader(name="路營二手", tax_id="12345678", address="台北市", phone="02-1234-5678")


def test_fakes_satisfy_protocols() -> None:
    assert isinstance(FakeLabelPrinter(), LabelPrinter)
    assert isinstance(FakeReceiptPrinter(), ReceiptPrinter)
    assert isinstance(FakeCashDrawer(), CashDrawer)
    assert isinstance(FakeStatusProvider(), DeviceStatusProvider)


def test_default_fake_devices_bundles_all() -> None:
    d = default_fake_devices()
    assert isinstance(d, AgentDevices)
    assert isinstance(d.label_printer, FakeLabelPrinter)
    assert isinstance(d.status_provider, FakeStatusProvider)


def test_fake_label_printer_records_and_simulates_failure() -> None:
    ok = FakeLabelPrinter()
    ok.print_label("ABC123", "帳篷", 1500)
    assert ok.labels == [("ABC123", "帳篷", 1500)]
    with pytest.raises(DeviceOffline):
        FakeLabelPrinter(offline=True).print_label("X", "n", 1)
    with pytest.raises(DeviceTimeout):
        FakeLabelPrinter(timeout=True).print_label("X", "n", 1)


def test_fake_receipt_printer_simulates_each_failure() -> None:
    ok = FakeReceiptPrinter()
    ok.print_receipt(_SALE, _HEADER)
    ok.print_detail(_SALE, _HEADER)
    assert ok.receipts == [(_SALE, _HEADER)]
    assert ok.details == [(_SALE, _HEADER)]
    with pytest.raises(DeviceOffline):
        FakeReceiptPrinter(offline=True).print_receipt(_SALE, _HEADER)
    with pytest.raises(PaperOut):
        FakeReceiptPrinter(paper_out=True).print_detail(_SALE, _HEADER)
    with pytest.raises(CoverOpen):
        FakeReceiptPrinter(cover_open=True).print_receipt(_SALE, _HEADER)
    with pytest.raises(DeviceTimeout):
        FakeReceiptPrinter(timeout=True).print_receipt(_SALE, _HEADER)


def test_fake_cash_drawer_connected_and_not_connected() -> None:
    ok = FakeCashDrawer()
    ok.open()
    assert ok.open_count == 1
    with pytest.raises(DrawerNotConnected):
        FakeCashDrawer(connected=False).open()


def test_fake_status_provider_defaults_three_devices() -> None:
    statuses = FakeStatusProvider().poll()
    models = {s.model for s in statuses}
    assert "Brother QL-810W" in models
    assert "EPSON TM-T82III" in models  # 與真機驅動 model 字串一致（ADR-011）
    brother = next(s for s in statuses if s.model == "Brother QL-810W")
    assert "paper_out" in brother.unsupported  # 網路下 B 級不做
    assert all(s.driver == "fake" for s in statuses)


async def test_label_endpoint_maps_offline_to_503() -> None:
    devices = default_fake_devices()
    app = create_app(
        AgentDevices(
            label_printer=FakeLabelPrinter(offline=True),
            receipt_printer=devices.receipt_printer,
            cash_drawer=devices.cash_drawer,
            status_provider=devices.status_provider,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/print/label", json={"code": "X", "name": "n", "price": 1})
    assert resp.status_code == 503
    assert resp.json()["error"] == "DeviceOffline"


async def test_drawer_endpoint_maps_not_connected_to_409() -> None:
    devices = default_fake_devices()
    app = create_app(
        AgentDevices(
            label_printer=devices.label_printer,
            receipt_printer=devices.receipt_printer,
            cash_drawer=FakeCashDrawer(connected=False),
            status_provider=devices.status_provider,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/drawer/open")
    assert resp.status_code == 409
    assert resp.json()["error"] == "DrawerNotConnected"


def test_create_app_rejects_legacy_printer_arg() -> None:
    """誤傳 Phase 0 的 SupportsWrite 印表機（非 AgentDevices）→ 早失敗、明確報錯。"""
    from agent.escpos_printer import FakePrinter

    with pytest.raises(TypeError, match="AgentDevices"):
        create_app(FakePrinter())  # type: ignore[arg-type]


async def test_injected_drawer_is_used_on_success() -> None:
    drawer = FakeCashDrawer()
    devices = default_fake_devices()
    app = create_app(
        AgentDevices(
            label_printer=devices.label_printer,
            receipt_printer=devices.receipt_printer,
            cash_drawer=drawer,
            status_provider=devices.status_provider,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/drawer/open")
    assert resp.status_code == 200
    assert drawer.open_count == 1
