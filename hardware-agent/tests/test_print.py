"""T15 列印端點測試：收據/明細聯印出抬頭、發票 placeholder、抬頭取不到/裝置失敗映射。

全程免實機：注入 FakeReceiptPrinter；店家抬頭 client 以覆寫依賴或 httpx.MockTransport mock。
"""

import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent.devices import AgentDevices, default_fake_devices
from agent.drivers.escpos_receipt import (
    _ITEM_HEADER,
    _WIDTH,
    EscposReceiptPrinter,
    _disp_width,
    _item_row,
)
from agent.escpos_printer import FakePrinter
from agent.fakes import FakeReceiptPrinter
from agent.interfaces import InvoicePayload, SaleLinePayload, SalePayload, StoreHeader
from agent.main import create_app
from agent.routers.print import get_store_header_client
from agent.store_client import StoreHeaderClient, StoreHeaderUnavailable

_AGENT_ROOT = Path(__file__).resolve().parent.parent


def test_print_router_importable_in_isolation() -> None:
    """回歸測試：agent.routers.print 必須能在全新直譯器中獨立匯入，
    防止 router↔main 循環匯入再度發生（DI 應從無循環的 agent.deps 取，而非 agent.main）。"""
    result = subprocess.run(
        [sys.executable, "-c", "import agent.routers.print as p; assert p.router"],
        cwd=_AGENT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


_SALE = SalePayload(
    id=1,
    store_id=7,
    subtotal="952",
    tax="48",
    total="1000",
    payment_method="CASH",
    invoice_status="NOT_ISSUED",
    created_at="2026-06-07T00:00:00Z",
    lines=[
        SaleLinePayload(
            line_type="CATALOG", description="帳篷", qty=1, unit_price="1000", line_total="1000"
        )
    ],
)
_HEADER = StoreHeader(name="路營二手", tax_id="12345678", address="台北市", phone="02-1234-5678")


class _FakeClient:
    """測試用抬頭 client：回傳固定抬頭或丟 StoreHeaderUnavailable。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def get_header(self, store_id: int) -> StoreHeader:
        self.calls += 1
        if self.fail:
            raise StoreHeaderUnavailable(f"store {store_id} 抬頭不可得")
        return _HEADER


def _app_with(printer: object, client: _FakeClient) -> FastAPI:
    base = default_fake_devices()
    app = create_app(
        AgentDevices(
            label_printer=base.label_printer,
            receipt_printer=printer,  # type: ignore[arg-type]
            cash_drawer=base.cash_drawer,
            status_provider=base.status_provider,
        )
    )
    app.dependency_overrides[get_store_header_client] = lambda: client
    return app


async def _post(app: object, path: str, json: dict[str, object] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=json)


async def test_print_receipt_includes_header() -> None:
    printer = FakeReceiptPrinter()
    resp = await _post(
        _app_with(printer, _FakeClient()), "/print/receipt", _SALE.model_dump(mode="json")
    )
    assert resp.status_code == 200
    assert printer.receipts == [(_SALE, _HEADER)]


async def test_print_detail_includes_header() -> None:
    printer = FakeReceiptPrinter()
    resp = await _post(
        _app_with(printer, _FakeClient()), "/print/detail", _SALE.model_dump(mode="json")
    )
    assert resp.status_code == 200
    assert printer.details == [(_SALE, _HEADER)]


async def test_print_einvoice_is_pending_placeholder() -> None:
    printer = FakeReceiptPrinter()
    resp = await _post(_app_with(printer, _FakeClient()), "/print/einvoice", {"sale_id": 1})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_einvoice_stage"
    assert printer.einvoices == [InvoicePayload(sale_id=1)]


async def test_receipt_header_unavailable_returns_503() -> None:
    printer = FakeReceiptPrinter()
    resp = await _post(
        _app_with(printer, _FakeClient(fail=True)), "/print/receipt", _SALE.model_dump(mode="json")
    )
    assert resp.status_code == 503
    assert printer.receipts == []  # 抬頭取不到 → 不印


async def test_receipt_printer_offline_returns_503() -> None:
    printer = FakeReceiptPrinter(offline=True)
    resp = await _post(
        _app_with(printer, _FakeClient()), "/print/receipt", _SALE.model_dump(mode="json")
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "DeviceOffline"


async def test_receipt_printer_paper_out_returns_409() -> None:
    printer = FakeReceiptPrinter(paper_out=True)
    resp = await _post(
        _app_with(printer, _FakeClient()), "/print/detail", _SALE.model_dump(mode="json")
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "PaperOut"


async def test_store_client_fetches_each_call_and_raises_without_cache() -> None:
    """抓取優先：每次都打後端取最新抬頭；無快取時後端失敗即丟 StoreHeaderUnavailable。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"name": "路營二手", "tax_id": "12345678", "address": "台北市", "phone": "02-1"},
        )

    client = StoreHeaderClient("http://backend", token="t", transport=httpx.MockTransport(handler))
    h1 = await client.get_header(7)
    h2 = await client.get_header(7)
    assert h1.name == "路營二手"
    assert h1 == h2
    assert len(requests) == 2  # 抓取優先：兩次都打後端（不再「快取優先」回舊值）
    assert requests[0].headers["Authorization"] == "Bearer t"

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    failing = StoreHeaderClient("http://backend", transport=httpx.MockTransport(fail_handler))
    with pytest.raises(StoreHeaderUnavailable):
        await failing.get_header(9)


async def test_store_client_fetch_first_reflects_backend_update() -> None:
    """後端更正抬頭（如換真統編）後，下次取得即拿到新值、不會永遠印舊快取。"""
    tax_ids = iter(["00000000", "12345678"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "露坑", "tax_id": next(tax_ids)})

    client = StoreHeaderClient("http://backend", transport=httpx.MockTransport(handler))
    first = await client.get_header(1)
    second = await client.get_header(1)
    assert first.tax_id == "00000000"
    assert second.tax_id == "12345678"  # 反映後端更正，非陳舊快取


async def test_store_client_falls_back_to_cache_on_backend_error() -> None:
    """後端暫時不可用時，退回最後一次成功取得的抬頭（抗暫時斷線；仍含店名/統編）。"""
    fail = False

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json={"name": "露坑", "tax_id": "12345678"})

    client = StoreHeaderClient("http://backend", transport=httpx.MockTransport(handler))
    warm = await client.get_header(1)  # 先成功一次、暖快取
    assert warm.tax_id == "12345678"
    fail = True
    fallback = await client.get_header(1)  # 後端 503 → 退回快取，不丟例外
    assert fallback == warm


@pytest.mark.parametrize("bad_tax_id", [None, "", "   "])
async def test_store_client_rejects_header_without_tax_id(bad_tax_id: str | None) -> None:
    """統編缺漏／空白 → 抬頭視為不可用（丟 StoreHeaderUnavailable）、且不得寫入快取。

    後端 schema 允許 tax_id=null（門市暫未設統編），但列印端要求抬頭完整：
    不可印出沒有賣方統編的收據（store_client / interfaces docstring 不變量）。
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"name": "路營二手", "tax_id": bad_tax_id})

    client = StoreHeaderClient("http://backend", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreHeaderUnavailable):
        await client.get_header(7)
    # 不得快取殘缺抬頭：再呼叫一次仍會重打後端並再次拒絕
    with pytest.raises(StoreHeaderUnavailable):
        await client.get_header(7)
    assert len(requests) == 2


async def test_store_client_rejects_blank_store_name() -> None:
    """店名空白（tax_id 正常）→ 抬頭不可用、不快取（不印沒有店名的收據）。"""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"name": "   ", "tax_id": "12345678"})

    client = StoreHeaderClient("http://backend", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreHeaderUnavailable):
        await client.get_header(7)
    with pytest.raises(StoreHeaderUnavailable):
        await client.get_header(7)
    assert len(requests) == 2  # 未快取殘缺抬頭


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),  # 非 JSON
        httpx.Response(200, json={"tax_id": "12345678"}),  # 缺必填 name，schema 不符
    ],
)
async def test_store_client_maps_malformed_response_to_unavailable(
    response: httpx.Response,
) -> None:
    """後端回 200 但 body 非 JSON／schema 不符 → 503（StoreHeaderUnavailable），非 500。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    client = StoreHeaderClient("http://backend", transport=httpx.MockTransport(handler))
    with pytest.raises(StoreHeaderUnavailable):
        await client.get_header(7)


def test_escpos_receipt_driver_renders_header_and_totals() -> None:
    writer = FakePrinter()
    EscposReceiptPrinter(writer).print_receipt(_SALE, _HEADER)
    buf = bytes(writer.buffer)
    # TM-T82III 繁中走 Big5 + FS & 中文模式（實機驗證 2026-06-08）
    assert b"\x1c&" in buf  # FS &：進入中文（Big5）模式
    assert "路營二手".encode("big5") in buf  # 抬頭以 Big5 編碼
    assert "路營二手".encode() not in buf  # 不再送 UTF-8（預設）——會在 TM-T82III 亂碼
    assert b"12345678" in buf  # 統編（ASCII 不變）
    assert "帳篷 x1".encode("big5") in buf
    assert "總計".encode("big5") in buf
    assert b"1000" in buf
    # 品項區欄位標題列
    assert "品項".encode("big5") in buf
    assert "單價".encode("big5") in buf
    assert "總價".encode("big5") in buf
    # 切紙前需進紙，讓結尾（總計區）通過切刀、不被切掉/殘留到下一張
    assert b"\n\n\n\n\x1dV\x00" in buf  # >=4 行進紙緊接 GS V 0 全切


def test_item_header_is_fixed_width() -> None:
    assert _disp_width(_ITEM_HEADER) == _WIDTH


def test_item_row_columns_are_fixed_width_and_right_aligned() -> None:
    line = SaleLinePayload(
        line_type="ITEM", description="防水外套（男款）", qty=2, unit_price="800", line_total="1600"
    )
    row = _item_row(line)
    assert _disp_width(row) == _WIDTH  # 整列固定寬，欄位對齊
    assert row.startswith("防水外套（男款） x2")  # 品名靠左
    assert row.endswith("1600")  # 總價靠右


def test_item_row_truncates_overlong_name_keeping_width() -> None:
    line = SaleLinePayload(
        line_type="ITEM", description="超" * 40, qty=1, unit_price="1", line_total="1"
    )
    row = _item_row(line)
    assert _disp_width(row) == _WIDTH  # 截斷後仍固定寬、不溢出折行
    assert ".." in row  # 截斷標記
    assert row.endswith(" 1")  # 單價/總價欄仍靠右對齊（不溢出）


def test_escpos_receipt_driver_detail_title_and_einvoice_placeholder() -> None:
    writer = FakePrinter()
    driver = EscposReceiptPrinter(writer)
    driver.print_detail(_SALE, _HEADER)
    detail_buf = bytes(writer.buffer)
    assert b"\x1c&" in detail_buf  # 中文模式
    assert "商品明細聯".encode("big5") in detail_buf

    writer2 = FakePrinter()
    EscposReceiptPrinter(writer2).print_einvoice(InvoicePayload(sale_id=1))
    einv_buf = bytes(writer2.buffer)
    assert b"\x1c&" in einv_buf
    assert "電子發票待發票收尾階段".encode("big5") in einv_buf
