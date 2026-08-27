"""雙印表機角色分離與逐機編碼（ADR-018）。

實機事實（2026-08-27，`GS I` 直接問印表機）：
- `.42` TM-T82III fw 15.05 S/N X7BJ020997 → 字型 ROM `TAIWAN BIG-5`
- `.44` TM-T82III fw 20.09 S/N X7B4090696 → 字型 ROM `CHINA GB18030`

兩台字型 ROM 不同，Big5 位元組送到後者是整捲亂碼；故（一）中文編碼必須隨機器走，
（二）發票專屬機只收證明聯、其餘紙一律走收據機。全程免實機。
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from agent.config import PrinterEndpoint
from agent.devices import AgentDevices, default_fake_devices
from agent.drivers.escpos_receipt import EscposReceiptPrinter, line_encoder
from agent.drivers.status_real import RealStatusProvider
from agent.escpos_printer import FakePrinter
from agent.fakes import FakeReceiptPrinter
from agent.interfaces import (
    AcquisitionReceiptItem,
    AcquisitionReceiptPayload,
    InvoicePayload,
    KitchenTicketLine,
    KitchenTicketPayload,
    SaleLinePayload,
    SalePayload,
    StoreHeader,
)
from agent.main import create_app
from agent.routers.print import get_store_header_client
from agent.store_client import StoreHeaderClient

_HEADER = StoreHeader(name="鹿營二手", tax_id="12345678", address="台北市", phone="02-1234-5678")
_SALE = SalePayload(
    id=42,
    store_id=1,
    subtotal="952",
    tax="48",
    total="1000",
    payment_method="CASH",
    invoice_status="ISSUED",
    created_at=datetime(2026, 8, 27, 3, 0, tzinfo=UTC),
    lines=[
        SaleLinePayload(
            line_type="CATALOG", description="帳篷", qty=1, unit_price="1000", line_total="1000"
        )
    ],
)
_INVOICE = InvoicePayload(
    sale_id=42,
    invoice_number="AB12345678",
    invoice_date=date(2026, 8, 27),
    invoice_time=time(11, 0, 0),
    random_code="9999",
    sales_amount="952",
    tax_amount="48",
    total_amount="1000",
    seller_tax_id="12345678",
    seller_name="鹿營二手",
    buyer_tax_id=None,
    lines=_SALE.lines,
    barcode_content="11508AB123456789999",
    qrcode_left_content="AB123456781150827" + "9" * 60,
    qrcode_right_content="**帳篷:1:1000",
)
_TICKET = KitchenTicketPayload(
    store_id=1,
    sale_id=42,
    service_mode="DINE_IN",
    table_no="A3",
    created_at=datetime(2026, 8, 27, 3, 0, tzinfo=UTC),
    lines=[KitchenTicketLine(description="手沖耶加雪菲", qty=1)],
)
_ACQUISITION = AcquisitionReceiptPayload(
    store_id=1,
    acquisition_id=7,
    seller_name="王小明",
    items=[AcquisitionReceiptItem(name="登山杖", amount="500")],
    total="500",
    payout_method="CASH",
    created_at=datetime(2026, 8, 27, 3, 0, tzinfo=UTC),
    # 1x1 全黑 RGBA PNG（signature_png 已驗過的最小合法輸入）
    signature_png_base64=(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    ),
)


# ── 逐機編碼 ──


class TestPerPrinterEncoding:
    def test_gbk_printer_emits_gbk_bytes_not_big5(self) -> None:
        """GB18030 機必須收到 GBK 位元組；收到 Big5 就是實機上那捲亂碼。"""
        buffer = FakePrinter()
        EscposReceiptPrinter(buffer, encoding="gbk").print_detail(_SALE, _HEADER)
        out = bytes(buffer.buffer)
        assert "商品明細聯".encode("gbk") in out
        assert "帳篷".encode("gbk") in out
        assert "商品明細聯".encode("big5") not in out

    def test_default_stays_big5(self) -> None:
        """未指定編碼＝既有單機部署（繁體機），版面位元組不得改變。"""
        buffer = FakePrinter()
        EscposReceiptPrinter(buffer).print_detail(_SALE, _HEADER)
        assert "商品明細聯".encode("big5") in bytes(buffer.buffer)

    def test_two_printers_encode_the_same_document_differently(self) -> None:
        """同一份 payload、兩台機器 → 兩份不同位元組。這正是本次改動存在的理由。"""
        big5_buf, gbk_buf = FakePrinter(), FakePrinter()
        EscposReceiptPrinter(big5_buf).print_detail(_SALE, _HEADER)
        EscposReceiptPrinter(gbk_buf, encoding="gbk").print_detail(_SALE, _HEADER)
        assert bytes(big5_buf.buffer) != bytes(gbk_buf.buffer)

    def test_kitchen_ticket_follows_the_printer_encoding(self) -> None:
        """出餐單也走收據機那台（GB18030），不可仍用 Big5。"""
        buffer = FakePrinter()
        EscposReceiptPrinter(buffer, encoding="gbk").print_kitchen_ticket(_TICKET)
        assert "出餐單".encode("gbk") in bytes(buffer.buffer)

    def test_unencodable_char_becomes_question_mark(self) -> None:
        """編不出的字退成 ?（看得出來），不得讓整張列印中斷。"""
        assert line_encoder("big5")("A\U0001f600B") == b"A?B\n"


# ── 角色解析 ──


class TestInvoicePrinterResolution:
    def test_falls_back_to_receipt_printer_when_absent(self) -> None:
        """沒接發票機 → 證明聯印回收據機（單機店家的既有行為，不得壞掉）。"""
        devices = default_fake_devices()
        assert devices.einvoice_printer is devices.receipt_printer

    def test_uses_invoice_printer_when_present(self) -> None:
        base = default_fake_devices()
        invoice = FakeReceiptPrinter()
        devices = AgentDevices(
            label_printer=base.label_printer,
            receipt_printer=base.receipt_printer,
            cash_drawer=base.cash_drawer,
            status_provider=base.status_provider,
            invoice_printer=invoice,
        )
        assert devices.einvoice_printer is invoice


# ── 端點去向 ──


def _app(receipt: FakeReceiptPrinter, invoice: FakeReceiptPrinter) -> FastAPI:
    base = default_fake_devices()
    app = create_app(
        AgentDevices(
            label_printer=base.label_printer,
            receipt_printer=receipt,
            cash_drawer=base.cash_drawer,
            status_provider=base.status_provider,
            invoice_printer=invoice,
        )
    )

    class _StubHeaderClient(StoreHeaderClient):
        def __init__(self) -> None:  # 不連後端
            pass

        async def get_header(self, store_id: int) -> StoreHeader:
            return _HEADER

    app.dependency_overrides[get_store_header_client] = lambda: _StubHeaderClient()
    return app


async def _post(app: FastAPI, path: str, json: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=json)


@pytest.fixture
def printers() -> tuple[FakeReceiptPrinter, FakeReceiptPrinter]:
    return FakeReceiptPrinter(), FakeReceiptPrinter()


async def test_einvoice_goes_to_the_invoice_printer(
    printers: tuple[FakeReceiptPrinter, FakeReceiptPrinter],
) -> None:
    receipt, invoice = printers
    resp = await _post(_app(receipt, invoice), "/print/einvoice", _INVOICE.model_dump(mode="json"))
    assert resp.status_code == 200
    assert invoice.einvoices == [_INVOICE]
    assert receipt.einvoices == []


async def test_invoice_reprint_raw_goes_to_the_invoice_printer(
    printers: tuple[FakeReceiptPrinter, FakeReceiptPrinter],
) -> None:
    """`/print/raw` 目前唯一用途是 Amego 的發票補印 → 一樣走發票機。"""
    receipt, invoice = printers
    resp = await _post(_app(receipt, invoice), "/print/raw", {"base64_data": "SGVsbG8="})
    assert resp.status_code == 200
    assert invoice.raw_prints == [b"Hello"]
    assert receipt.raw_prints == []


async def test_detail_never_reaches_the_invoice_printer(
    printers: tuple[FakeReceiptPrinter, FakeReceiptPrinter],
) -> None:
    """發票機是**發票專屬**：明細聯跑過去就是印在錯的紙上、錯的編碼上。"""
    receipt, invoice = printers
    resp = await _post(_app(receipt, invoice), "/print/detail", _SALE.model_dump(mode="json"))
    assert resp.status_code == 200
    assert len(receipt.details) == 1
    assert invoice.details == []


async def test_receipt_never_reaches_the_invoice_printer(
    printers: tuple[FakeReceiptPrinter, FakeReceiptPrinter],
) -> None:
    receipt, invoice = printers
    resp = await _post(_app(receipt, invoice), "/print/receipt", _SALE.model_dump(mode="json"))
    assert resp.status_code == 200
    assert len(receipt.receipts) == 1
    assert invoice.receipts == []


async def test_acquisition_never_reaches_the_invoice_printer(
    printers: tuple[FakeReceiptPrinter, FakeReceiptPrinter],
) -> None:
    receipt, invoice = printers
    resp = await _post(
        _app(receipt, invoice), "/print/acquisition", _ACQUISITION.model_dump(mode="json")
    )
    assert resp.status_code == 200
    assert len(receipt.acquisitions) == 1
    assert invoice.acquisitions == []


async def test_kitchen_never_reaches_the_invoice_printer(
    printers: tuple[FakeReceiptPrinter, FakeReceiptPrinter],
) -> None:
    receipt, invoice = printers
    resp = await _post(_app(receipt, invoice), "/print/kitchen", _TICKET.model_dump(mode="json"))
    assert resp.status_code == 200
    assert receipt.kitchen_tickets == [_TICKET]
    assert invoice.kitchen_tickets == []


# ── 狀態列管 ──


@contextmanager
def _mock_tcp(outcomes: Mapping[str, Any]) -> Generator[MagicMock, None, None]:
    """patch `socket.create_connection`，依 host 決定成敗（確保不碰真實硬體）。

    與 `tests/test_status_real.py` 同一手法；未列出的 host 視為連線被拒。
    """

    def fake_create_connection(address: tuple[str, int], timeout: float | None = None) -> Any:
        if outcomes.get(address[0]) != "ok":
            raise ConnectionRefusedError("refused")
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        return sock

    with patch("socket.create_connection", side_effect=fake_create_connection) as mock_conn:
        yield mock_conn


class TestStatusIncludesInvoicePrinter:
    def test_invoice_printer_is_listed(self) -> None:
        """發票機必須列管：它離線時，發票印不出來而店員在櫃檯不一定看得到那台。"""
        provider = RealStatusProvider(
            epson=PrinterEndpoint(host="203.0.113.44"),
            invoice=PrinterEndpoint(host="203.0.113.42"),
            drawer=PrinterEndpoint(host="203.0.113.42"),
        )
        ids = [d.id for d in provider.poll()]
        assert "invoice-1" in ids

    def test_absent_invoice_printer_is_not_listed(self) -> None:
        """沒接就不列——狀態頁不得多出一台不存在的機器。"""
        provider = RealStatusProvider(
            epson=PrinterEndpoint(host="203.0.113.44"),
            drawer=PrinterEndpoint(host="203.0.113.44"),
        )
        assert "invoice-1" not in [d.id for d in provider.poll()]

    def test_drawer_follows_its_own_printer_not_the_receipt_printer(self) -> None:
        """錢櫃線插在發票機那台：**收據機在線不代表錢櫃踢得動**。

        刻意讓收據機在線、發票機離線——舊行為（錢櫃依附收據機）會回報錢櫃在線，
        店員按了才發現踢不動。
        """
        provider = RealStatusProvider(
            epson=PrinterEndpoint(host="203.0.113.44"),
            invoice=PrinterEndpoint(host="203.0.113.42"),
            drawer=PrinterEndpoint(host="203.0.113.42"),
        )
        with _mock_tcp({"203.0.113.44": "ok"}):  # 發票機未列出＝連線被拒
            statuses = {d.id: d for d in provider.poll()}
        assert statuses["epson-1"].online is True
        assert statuses["invoice-1"].online is False
        assert statuses["drawer-1"].online is False

    def test_drawer_still_follows_the_receipt_printer_when_not_separated(self) -> None:
        """未拆錢櫃設定的單機部署：行為維持原樣（錢櫃跟著收據機）。"""
        endpoint = PrinterEndpoint(host="203.0.113.44")
        provider = RealStatusProvider(epson=endpoint, drawer=endpoint)
        with _mock_tcp({"203.0.113.44": "ok"}):
            statuses = {d.id: d for d in provider.poll()}
        assert statuses["drawer-1"].online is True

    def test_shared_endpoint_is_probed_once_per_poll(self) -> None:
        """錢櫃與發票機同一台 → 只開一條 TCP：每多一次探測就多等一個逾時。"""
        invoice = PrinterEndpoint(host="203.0.113.42")
        provider = RealStatusProvider(
            epson=PrinterEndpoint(host="203.0.113.44"), invoice=invoice, drawer=invoice
        )
        with _mock_tcp({"203.0.113.44": "ok", "203.0.113.42": "ok"}) as mock_conn:
            provider.poll()
        assert mock_conn.call_count == 2
