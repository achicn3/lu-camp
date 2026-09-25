"""收購佇列收件單列印（docs/42 裁示 3）：收據機印兩份相同的收件單（客人聯＋商品聯）。

同一批只有一個號碼、一個條碼；號碼用三倍字（限 ASCII），條碼用 Code39（之後掃了直接打開那一批）。
"""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from agent.devices import AgentDevices, default_fake_devices
from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.escpos_printer import FakePrinter
from agent.fakes import FakeReceiptPrinter
from agent.interfaces import IntakeSlipPayload
from agent.main import create_app

_SLIP = IntakeSlipPayload(
    store_id=1,
    batch_id=123,
    label="A007",
    slip_code="IN000123",
    seller_name="王小明",
    declared_item_count=3,
    created_at=datetime(2026, 9, 25, 3, 0, tzinfo=UTC),
)
_CUT = b"\x1dV"


def _app(receipt: FakeReceiptPrinter, invoice: FakeReceiptPrinter) -> FastAPI:
    base = default_fake_devices()
    return create_app(
        AgentDevices(
            label_printer=base.label_printer,
            receipt_printer=receipt,
            cash_drawer=base.cash_drawer,
            status_provider=base.status_provider,
            invoice_printer=invoice,
        )
    )


async def _post(app: FastAPI, json: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/print/intake-slip", json=json)


def test_prints_two_identical_copies_each_cut() -> None:
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_intake_slip(_SLIP)
    data = bytes(buf.buffer)
    assert data.count(_CUT) == 2
    assert data.count(b"A007") == 2
    assert data.count(b"IN000123") == 2  # 條碼下方印出內容，掃不到時可手打
    assert "客人聯".encode("big5") in data and "商品聯".encode("big5") in data
    assert "王小明".encode("big5") in data


def test_reprint_can_ask_for_a_single_copy() -> None:
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_intake_slip(_SLIP.model_copy(update={"copies": 1}))
    data = bytes(buf.buffer)
    assert data.count(_CUT) == 1
    assert "補印".encode("big5") in data and "客人聯".encode("big5") not in data


@pytest.mark.parametrize(
    "update", [{"label": "七號"}, {"slip_code": "in-123"}, {"copies": 0}, {"copies": 4}]
)
def test_payload_limits(update: dict[str, object]) -> None:
    """號碼走三倍字（限 ASCII）、條碼走 Code39（限大寫英數與 -）；份數 1–3。"""
    with pytest.raises(ValidationError):
        IntakeSlipPayload.model_validate({**_SLIP.model_dump(), **update})


async def test_goes_to_the_receipt_printer_only() -> None:
    receipt, invoice = FakeReceiptPrinter(), FakeReceiptPrinter()
    resp = await _post(_app(receipt, invoice), _SLIP.model_dump(mode="json"))
    assert resp.status_code == 200
    assert receipt.intake_slips == [_SLIP]
    assert invoice.intake_slips == []


async def test_paper_out_is_reported() -> None:
    """缺紙要如實回錯：店員以為客人拿到收件單了，其實沒有。"""
    printer = FakeReceiptPrinter(paper_out=True)
    resp = await _post(_app(printer, FakeReceiptPrinter()), _SLIP.model_dump(mode="json"))
    assert resp.status_code == 409
    assert resp.json()["error"] == "PaperOut"
