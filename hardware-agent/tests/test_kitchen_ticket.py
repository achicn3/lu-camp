"""出餐單測試（docs/35）：payload fail-closed 驗證、ESC/POS 版面、`/print/kitchen` 端點。

出餐單是**內部作業單**：不取店家抬頭、不印任何金額、只印餐飲（MENU）品項。
全程免實機：驅動寫入 byte buffer 驗版面，端點注入 FakeReceiptPrinter 驗錯誤映射。
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
from agent.interfaces import KitchenTicketLine, KitchenTicketPayload
from agent.main import create_app

_LINES = [
    KitchenTicketLine(description="手沖耶加雪菲", qty=1),
    KitchenTicketLine(description="鮮奶茶（無糖）", qty=2),
]


def _dine_in(**overrides: object) -> KitchenTicketPayload:
    data: dict[str, object] = {
        "store_id": 7,
        "sale_id": 1042,
        "service_mode": "DINE_IN",
        "table_no": "A3",
        "created_at": datetime(2026, 8, 17, 6, 32, tzinfo=UTC),  # 台北 14:32
        "lines": _LINES,
    }
    data.update(overrides)
    return KitchenTicketPayload(**data)


# ── payload：fail closed ──


def test_dine_in_requires_table_no() -> None:
    """內用缺桌號 → 拒收。默默印出沒有桌號的內用單＝東西送不出去。"""
    with pytest.raises(ValidationError):
        _dine_in(table_no=None)


def test_dine_in_rejects_blank_table_no() -> None:
    """只有空白的桌號等同沒填（前端 trim 後送出空字串亦擋）。"""
    with pytest.raises(ValidationError):
        _dine_in(table_no="   ")


def test_takeout_rejects_table_no() -> None:
    """外帶夾帶桌號 → 拒收（呼叫端版本錯配，不可默默印成內用單）。"""
    with pytest.raises(ValidationError):
        _dine_in(service_mode="TAKEOUT", table_no="A3")


def test_takeout_without_table_no_is_valid() -> None:
    ticket = _dine_in(service_mode="TAKEOUT", table_no=None)
    assert ticket.table_no is None


def test_rejects_empty_lines() -> None:
    """沒有餐飲品項就不該產生出餐單。"""
    with pytest.raises(ValidationError):
        _dine_in(lines=[])


def test_rejects_non_positive_qty() -> None:
    with pytest.raises(ValidationError):
        KitchenTicketLine(description="美式咖啡", qty=0)


# ── 驅動版面 ──


def test_kitchen_ticket_layout_dine_in() -> None:
    writer = FakePrinter()
    EscposReceiptPrinter(writer).print_kitchen_ticket(_dine_in())
    buf = bytes(writer.buffer)
    assert b"\x1c&" in buf  # FS &：中文（Big5）模式
    assert "出餐單".encode("big5") in buf
    assert "內用".encode("big5") in buf
    assert "桌號".encode("big5") in buf
    assert "A3".encode("big5") in buf
    assert "手沖耶加雪菲".encode("big5") in buf
    assert "鮮奶茶（無糖）".encode("big5") in buf
    assert b"x2" in buf  # 數量
    assert b"#1042" in buf  # 單號
    assert b"08-17 14:32" in buf  # 台北時區（payload 為 UTC 06:32）
    assert b"\n\n\n\n\x1dV\x00" in buf  # 進紙後全切


def test_kitchen_ticket_table_no_is_double_size() -> None:
    """桌號要放大——吧台是隔著距離掃一眼的，跟內文同字級等於沒有。"""
    writer = FakePrinter()
    EscposReceiptPrinter(writer).print_kitchen_ticket(_dine_in())
    buf = bytes(writer.buffer)
    double_on, double_off = b"\x1d!\x11\x1cW\x01", b"\x1d!\x00\x1cW\x00"
    # 掃過所有雙倍字區塊（標題也是放大的），桌號必須落在其中之一。
    blocks, cursor = [], 0
    while (start := buf.find(double_on, cursor)) != -1:
        end = buf.index(double_off, start)
        blocks.append(buf[start:end])
        cursor = end
    assert any("A3".encode("big5") in block for block in blocks)


def test_kitchen_ticket_layout_takeout_has_no_table_row() -> None:
    writer = FakePrinter()
    EscposReceiptPrinter(writer).print_kitchen_ticket(
        _dine_in(service_mode="TAKEOUT", table_no=None)
    )
    buf = bytes(writer.buffer)
    assert "外帶".encode("big5") in buf
    assert "桌號".encode("big5") not in buf
    assert "內用".encode("big5") not in buf


def test_kitchen_ticket_prints_no_amounts() -> None:
    """出餐單不是給客人的憑證：不得出現任何金額，也不印店家抬頭。"""
    writer = FakePrinter()
    EscposReceiptPrinter(writer).print_kitchen_ticket(_dine_in())
    buf = bytes(writer.buffer)
    for token in ("總計", "未稅", "營業稅", "付款", "統一編號"):
        assert token.encode("big5") not in buf


# ── 端點 ──


def _app_with(printer: object) -> FastAPI:
    base = default_fake_devices()
    return create_app(
        AgentDevices(
            label_printer=base.label_printer,
            receipt_printer=printer,  # type: ignore[arg-type]
            cash_drawer=base.cash_drawer,
            status_provider=base.status_provider,
        )
    )


async def _post(app: object, path: str, json: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=json)


async def test_print_kitchen_endpoint_needs_no_store_header() -> None:
    """**未覆寫抬頭 client 依賴**仍須成功：出餐單不該相依後端 `stores`。"""
    printer = FakeReceiptPrinter()
    ticket = _dine_in()
    resp = await _post(_app_with(printer), "/print/kitchen", ticket.model_dump(mode="json"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert printer.kitchen_tickets == [ticket]


async def test_print_kitchen_rejects_invalid_payload() -> None:
    printer = FakeReceiptPrinter()
    bad = {**_dine_in().model_dump(mode="json"), "table_no": None}
    resp = await _post(_app_with(printer), "/print/kitchen", bad)
    assert resp.status_code == 422
    assert printer.kitchen_tickets == []


async def test_print_kitchen_paper_out_returns_409() -> None:
    printer = FakeReceiptPrinter(paper_out=True)
    resp = await _post(
        _app_with(printer), "/print/kitchen", _dine_in().model_dump(mode="json")
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "PaperOut"


async def test_print_kitchen_offline_returns_503() -> None:
    printer = FakeReceiptPrinter(offline=True)
    resp = await _post(
        _app_with(printer), "/print/kitchen", _dine_in().model_dump(mode="json")
    )
    assert resp.status_code == 503
    assert resp.json()["error"] == "DeviceOffline"


# ── 稅務識別欄位必須是 ASCII（Codex 對抗審查第二輪 high）──


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("invoice_number", "ZA１２３４５６７８"),  # 全形
        ("invoice_number", "ZA١٢٣٤٥٦٧٨"),  # 阿拉伯-印度
        ("random_code", "１２３４"),
        ("total_amount", "１０５０"),
        ("seller_tax_id", "１２３４５６７８"),
    ],
)
def test_invoice_payload_rejects_non_ascii_tax_fields(field: str, value: str) -> None:
    """否則會印出「肉眼看到的號碼/金額/統編」與條碼內容不一致的證明聯。"""
    from datetime import date, time

    from agent.interfaces import InvoicePayload, SaleLinePayload

    base: dict[str, object] = {
        "sale_id": 1,
        "invoice_number": "AB12345678",
        "invoice_date": date(2026, 8, 17),
        "invoice_time": time(14, 32),
        "random_code": "9999",
        "sales_amount": "1000",
        "tax_amount": "50",
        "total_amount": "1050",
        "seller_tax_id": "12345678",
        "seller_name": "路營二手",
        "lines": [
            SaleLinePayload(
                line_type="CATALOG",
                description="帳篷",
                qty=1,
                unit_price="1050",
                line_total="1050",
            )
        ],
        "barcode_content": "11508AB123456789999",
        "qrcode_left_content": "L",
        "qrcode_right_content": "R",
    }
    base[field] = value
    with pytest.raises(ValidationError):
        InvoicePayload(**base)


# ── 第二台印表機（出餐機；docs/35）──


def _app_with_kitchen(receipt: object, kitchen: object) -> FastAPI:
    base = default_fake_devices()
    return create_app(
        AgentDevices(
            label_printer=base.label_printer,
            receipt_printer=receipt,  # type: ignore[arg-type]
            cash_drawer=base.cash_drawer,
            status_provider=base.status_provider,
            kitchen_printer=kitchen,  # type: ignore[arg-type]
        )
    )


async def test_kitchen_ticket_goes_to_kitchen_printer_when_configured() -> None:
    """接了第二台就印到那台，**收據機不得也收到一份**。

    兩台都印＝廚房和櫃檯各出一張，店員以為有兩筆單。
    """
    receipt = FakeReceiptPrinter()
    kitchen = FakeReceiptPrinter()
    ticket = _dine_in()
    resp = await _post(
        _app_with_kitchen(receipt, kitchen), "/print/kitchen", ticket.model_dump(mode="json")
    )
    assert resp.status_code == 200
    assert kitchen.kitchen_tickets == [ticket]
    assert receipt.kitchen_tickets == []


async def test_kitchen_ticket_falls_back_to_receipt_printer() -> None:
    """**沒接第二台就退回收據機**：買到機器之前，出餐單不得因此壞掉。"""
    receipt = FakeReceiptPrinter()
    ticket = _dine_in()
    resp = await _post(_app_with(receipt), "/print/kitchen", ticket.model_dump(mode="json"))
    assert resp.status_code == 200
    assert receipt.kitchen_tickets == [ticket]


async def test_kitchen_printer_receives_only_kitchen_tickets() -> None:
    """出餐機不得收到收據/明細聯/證明聯：客人的憑證仍從櫃檯那台出。"""
    receipt = FakeReceiptPrinter()
    kitchen = FakeReceiptPrinter()
    resp = await _post(
        _app_with_kitchen(receipt, kitchen), "/print/kitchen", _dine_in().model_dump(mode="json")
    )
    assert resp.status_code == 200
    assert kitchen.kitchen_tickets == [_dine_in()]
    assert (kitchen.receipts, kitchen.details, kitchen.einvoices) == ([], [], [])


async def test_kitchen_printer_paper_out_does_not_fall_back_to_receipt() -> None:
    """出餐機缺紙**不得**偷偷改印櫃檯那台：店員會以為廚房收到了。"""
    receipt = FakeReceiptPrinter()
    kitchen = FakeReceiptPrinter(paper_out=True)
    resp = await _post(
        _app_with_kitchen(receipt, kitchen), "/print/kitchen", _dine_in().model_dump(mode="json")
    )
    assert resp.status_code == 409
    assert receipt.kitchen_tickets == []
