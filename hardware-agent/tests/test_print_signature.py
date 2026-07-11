"""K6 憑證聯/明細聯簽名列印（docs/23，D6）：明細聯購物金＋簽名尾段、收購憑證聯端點與版面。

全程免實機：EscposReceiptPrinter 寫入 FakePrinter byte buffer 驗版面；路由以
FakeReceiptPrinter 驗端點與錯誤映射。
"""

from __future__ import annotations

import base64
import zlib

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from agent.devices import AgentDevices, default_fake_devices
from agent.drivers.escpos_receipt import EscposReceiptPrinter
from agent.escpos_printer import FakePrinter
from agent.fakes import FakeReceiptPrinter
from agent.interfaces import (
    AcquisitionReceiptItem,
    AcquisitionReceiptPayload,
    SaleLinePayload,
    SalePayload,
    StoreHeader,
)
from agent.main import create_app
from agent.routers.print import get_store_header_client
from agent.store_client import StoreHeaderUnavailable

_HEADER = StoreHeader(name="路營二手", tax_id="12345678", address="台北市", phone="02-1234-5678")
_RASTER_PREFIX = b"\x1dv0"  # GS v 0


def _signature_b64(width: int = 200, height: int = 80) -> str:
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def _sale(**overrides: object) -> SalePayload:
    base: dict[str, object] = {
        "id": 1,
        "store_id": 1,
        "subtotal": "952",
        "tax": "48",
        "total": "1000",
        "payment_method": "STORE_CREDIT",
        "invoice_status": "NOT_ISSUED",
        "created_at": "2026-07-10T00:00:00Z",
        "lines": [
            SaleLinePayload(
                line_type="CATALOG", description="帳篷", qty=1, unit_price="1000", line_total="1000"
            )
        ],
    }
    base.update(overrides)
    return SalePayload.model_validate(base)


def _acq_receipt(**overrides: object) -> AcquisitionReceiptPayload:
    base: dict[str, object] = {
        "store_id": 1,
        "acquisition_id": 77,
        "seller_name": "賣家甲",
        "items": [AcquisitionReceiptItem(name="登山外套", amount="1200")],
        "total": "1200",
        "payout_method": "STORE_CREDIT",
        "created_at": "2026-07-10T10:30:00Z",
        "signature_png_base64": _signature_b64(),
        "store_credit_granted": "1320",
        "store_credit_balance_after": "2520",
    }
    base.update(overrides)
    return AcquisitionReceiptPayload.model_validate(base)


def _big5(text: str) -> bytes:
    return text.encode("big5")


def test_detail_with_store_credit_and_signature_renders_tail() -> None:
    buf = FakePrinter()
    printer = EscposReceiptPrinter(buf)
    sale = _sale(
        store_credit_deducted="300",
        store_credit_remaining="700",
        signature_png_base64=_signature_b64(),
    )
    printer.print_detail(sale, _HEADER)
    data = bytes(buf.buffer)
    assert _big5("購物金折抵 -300") in data
    assert _big5("購物金剩餘  700") in data
    assert _big5("客戶簽名：") in data
    assert _RASTER_PREFIX in data  # 簽名光柵


def test_detail_without_optional_fields_unchanged() -> None:
    """未帶 K6 欄位時版面不含購物金/簽名段（向後相容）。"""
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_detail(_sale(), _HEADER)
    data = bytes(buf.buffer)
    assert _big5("購物金折抵") not in data
    assert _big5("客戶簽名") not in data
    assert _RASTER_PREFIX not in data


def test_acquisition_receipt_layout() -> None:
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_acquisition(_acq_receipt(), _HEADER)
    data = bytes(buf.buffer)
    for text in (
        "收購憑證聯",
        "收購單號 #77",
        "賣方 賣家甲",
        "登山外套",
        "收購總額 1200",
        "撥款方式：購物金",
        "撥入購物金 +1320",
        "購物金總額 2520",
        "賣方簽名：",
    ):
        assert _big5(text) in data, text
    assert _RASTER_PREFIX in data
    assert _big5("路營二手") in data  # 抬頭
    # 「活餘額」（列印當下另查的餘額）仍不可入憑證（Codex K6 第二輪）；「購物金總額」
    # 是本筆撥款分錄燒進帳本的 balance_after（本筆交易事實，2026-07-11 裁示要求加印）。
    assert _big5("購物金餘額") not in data


def test_acquisition_receipt_cash_payout_omits_credit_lines() -> None:
    buf = FakePrinter()
    receipt = _acq_receipt(
        payout_method="CASH", store_credit_granted=None, store_credit_balance_after=None
    )
    EscposReceiptPrinter(buf).print_acquisition(receipt, _HEADER)
    data = bytes(buf.buffer)
    assert _big5("撥款方式：現金") in data
    assert _big5("撥入購物金") not in data
    assert _big5("購物金總額") not in data


def test_store_credit_receipt_requires_credit_facts() -> None:
    """STORE_CREDIT 憑證缺撥入額或總額 → 拒收（Codex：不得印出缺必要金額事實的存證聯）。"""
    with pytest.raises(ValidationError):
        _acq_receipt(store_credit_granted=None)
    with pytest.raises(ValidationError):
        _acq_receipt(store_credit_balance_after=None)
    with pytest.raises(ValidationError):
        _acq_receipt(store_credit_granted="abc")  # 非整數元字串
    with pytest.raises(ValidationError):
        _acq_receipt(store_credit_balance_after="1,320")


def test_cash_receipt_rejects_credit_facts() -> None:
    """CASH 憑證不得夾帶購物金欄位（版本錯配/呼叫端誤傳 → 拒收，不默默略過）。"""
    with pytest.raises(ValidationError):
        _acq_receipt(payout_method="CASH", store_credit_balance_after=None)
    with pytest.raises(ValidationError):
        _acq_receipt(payout_method="CASH", store_credit_granted=None)


def test_unknown_payout_method_rejected() -> None:
    """SPLIT/未知撥款值 → 拒收（Codex 第二輪：不得默默印成現金憑證）。"""
    for method in ("SPLIT", "cash", "FOO"):
        with pytest.raises(ValidationError):
            _acq_receipt(
                payout_method=method, store_credit_granted=None, store_credit_balance_after=None
            )
        with pytest.raises(ValidationError):
            _acq_receipt(payout_method=method)


class _FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def get_header(self, store_id: int) -> StoreHeader:
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


async def _post(app: object, path: str, json: dict[str, object]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, json=json)


async def test_print_acquisition_endpoint_ok() -> None:
    printer = FakeReceiptPrinter()
    receipt = _acq_receipt()
    resp = await _post(
        _app_with(printer, _FakeClient()),
        "/print/acquisition",
        receipt.model_dump(mode="json"),
    )
    assert resp.status_code == 200, resp.text
    assert printer.acquisitions == [(receipt, _HEADER)]


async def test_print_acquisition_header_unavailable_503() -> None:
    resp = await _post(
        _app_with(FakeReceiptPrinter(), _FakeClient(fail=True)),
        "/print/acquisition",
        _acq_receipt().model_dump(mode="json"),
    )
    assert resp.status_code == 503


async def test_bad_signature_png_returns_422() -> None:
    """壞簽名影像（非 PNG）→ 422，不印壞證據（真驅動路徑）。"""
    buf = FakePrinter()
    app_printer = EscposReceiptPrinter(buf)
    receipt = _acq_receipt(
        signature_png_base64=base64.b64encode(b"garbage").decode()
    )
    resp = await _post(
        _app_with(app_printer, _FakeClient()),
        "/print/acquisition",
        receipt.model_dump(mode="json"),
    )
    assert resp.status_code == 422, resp.text
    assert _RASTER_PREFIX not in bytes(buf.buffer)


async def test_blank_signature_rejected_end_to_end() -> None:
    """空白簽名（全白）→ 422（簽名證據不可為空白）。"""
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    raw = bytearray()
    for _y in range(80):
        raw.append(0)
        raw += b"\xff\xff\xff\xff" * 200
    ihdr = (200).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    blank = base64.b64encode(
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    ).decode()
    buf = FakePrinter()
    resp = await _post(
        _app_with(EscposReceiptPrinter(buf), _FakeClient()),
        "/print/acquisition",
        _acq_receipt(signature_png_base64=blank).model_dump(mode="json"),
    )
    assert resp.status_code == 422, resp.text


def test_print_area_set_before_signature_raster() -> None:
    """GS W（印字區 408 dots）必須先於簽名 GS v 0（Codex K6 第一輪：TM-T82III 預設以 576 dots
    置中，360-dot 簽名會右緣裁切、毀損簽名證據）。"""
    gs_w = b"\x1dW"
    for doc_bytes in (
        _detail_bytes(),
        _acquisition_bytes(),
    ):
        w = doc_bytes.find(gs_w)
        raster = doc_bytes.find(_RASTER_PREFIX)
        assert w != -1 and raster != -1
        assert w < raster, "GS W 須先於簽名光柵"


def _detail_bytes() -> bytes:
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_detail(
        _sale(
            store_credit_deducted="300",
            store_credit_remaining="700",
            signature_png_base64=_signature_b64(),
        ),
        _HEADER,
    )
    return bytes(buf.buffer)


def _acquisition_bytes() -> bytes:
    buf = FakePrinter()
    EscposReceiptPrinter(buf).print_acquisition(_acq_receipt(), _HEADER)
    return bytes(buf.buffer)


def test_receipt_timestamp_in_store_timezone() -> None:
    """憑證時間以店面時區（Asia/Taipei）呈現：UTC 2026-07-10 17:30 → 台北 07-11 01:30
    （跨日案例；Codex K6 第四輪）。"""
    buf = FakePrinter()
    receipt = _acq_receipt(created_at="2026-07-10T17:30:00Z")
    EscposReceiptPrinter(buf).print_acquisition(receipt, _HEADER)
    data = bytes(buf.buffer)
    assert _big5("日期 2026-07-11 01:30") in data
    assert _big5("2026-07-10 17:30") not in data


def test_payment_method_printed_in_chinese() -> None:
    """付款方式印中文（收據給客人看，不印 STORE_CREDIT 代碼）。"""
    for method, label in (("CASH", "現金"), ("STORE_CREDIT", "購物金"), ("MIXED", "現金＋購物金")):
        buf = FakePrinter()
        EscposReceiptPrinter(buf).print_detail(_sale(payment_method=method), _HEADER)
        data = bytes(buf.buffer)
        assert _big5(f"付款方式：{label}") in data
        assert method.encode() not in data
