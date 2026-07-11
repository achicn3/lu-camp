"""Amego 光貿 API 客戶端與 payload builder 單元測試（docs/24；純函式、無 DB）。

規格來源：docs/24-amego-einvoice.md（api_doc 2026-06-10 版）。
- 簽章：sign = md5(data JSON 字串 + time + App Key)。
- f0401 金額（含稅品項）：SalesAmount=Σ含稅小計；B2C TaxAmount=0；
  B2B TaxAmount = Sales − Round(Sales/1.05)、SalesAmount −= TaxAmount
  （與 split_tax_inclusive 同式）。
"""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.einvoice.amego import (
    AmegoClient,
    amego_order_id,
    build_f0401_data,
    build_f0501_data,
    build_invoice_query_data,
    sign_form,
)
from app.modules.einvoice.models import Invoice
from app.modules.sales.models import SaleLine
from app.shared.enums import InvoiceStatus, InvoiceType, SaleLineType
from app.shared.exceptions import AmegoNotConfigured


def _line(description: str, qty: int, unit_price: str, line_total: str) -> SaleLine:
    return SaleLine(
        store_id=1,
        sale_id=7,
        line_type=SaleLineType.CATALOG,
        description=description,
        qty=qty,
        unit_price=Decimal(unit_price),
        line_total=Decimal(line_total),
    )


def _invoice(**overrides: object) -> Invoice:
    base: dict[str, object] = {
        "store_id": 1,
        "sale_id": 7,
        "invoice_type": InvoiceType.B2C,
        "net": Decimal(952),
        "tax": Decimal(48),
        "total": Decimal(1000),
        "status": InvoiceStatus.PENDING,
        "donate_mark": False,
        "print_mark": True,
    }
    base.update(overrides)
    return Invoice(**base)


def test_sign_form_md5_of_data_time_key() -> None:
    data = '{"OrderId":"S1-7"}'
    expected = hashlib.md5(f"{data}1700000000sHeq7t8G1wiQvhAuIM27".encode()).hexdigest()
    assert sign_form(data, 1700000000, "sHeq7t8G1wiQvhAuIM27") == expected


def test_order_id_deterministic_per_sale() -> None:
    assert amego_order_id(store_id=1, sale_id=7) == "S1-7"
    assert amego_order_id(store_id=12, sale_id=3456) == "S12-3456"


def test_f0401_b2c_amounts_tax_zero() -> None:
    """B2C（無統編）：SalesAmount 維持含稅、TaxAmount=0（doc 含稅商品金額計算邏輯）。"""
    inv = _invoice()
    data = build_f0401_data(
        inv,
        [_line("帳篷", 1, "1000", "1000")],
        order_id="S1-7",
        tax_rate=Decimal("0.05"),
    )
    assert data["OrderId"] == "S1-7"
    assert data["BuyerIdentifier"] == "0000000000"
    assert data["BuyerName"] == "消費者"
    assert data["SalesAmount"] == 1000
    assert data["TaxAmount"] == 0
    assert data["FreeTaxSalesAmount"] == 0
    assert data["ZeroTaxSalesAmount"] == 0
    assert data["TotalAmount"] == 1000
    assert data["TaxType"] == 1
    assert data["TaxRate"] == "0.05"
    item = data["ProductItem"][0]
    assert item == {
        "Description": "帳篷",
        "Quantity": 1,
        "UnitPrice": "1000",
        "Amount": "1000",
        "TaxType": 1,
    }


def test_f0401_b2b_split_tax() -> None:
    """B2B（打統編）：TaxAmount = 1000 − Round(1000/1.05) = 48、SalesAmount = 952。"""
    inv = _invoice(
        invoice_type=InvoiceType.B2B, buyer_tax_id="12345678", buyer_name="測試環境有限公司"
    )
    data = build_f0401_data(
        inv,
        [_line("帳篷", 1, "1000", "1000")],
        order_id="S1-7",
        tax_rate=Decimal("0.05"),
    )
    assert data["BuyerIdentifier"] == "12345678"
    assert data["BuyerName"] == "測試環境有限公司"
    assert data["SalesAmount"] == 952
    assert data["TaxAmount"] == 48
    assert data["TotalAmount"] == 1000


def test_f0401_carrier_and_donation_fields() -> None:
    carrier = _invoice(carrier_type="3J0002", carrier_id="/ABC+123", print_mark=False)
    data = build_f0401_data(
        carrier, [_line("帳篷", 1, "1000", "1000")], order_id="S1-7", tax_rate=Decimal("0.05")
    )
    assert data["CarrierType"] == "3J0002"
    assert data["CarrierId1"] == "/ABC+123"
    assert data["CarrierId2"] == "/ABC+123"
    assert "NPOBAN" not in data

    donate = _invoice(donate_mark=True, npoban="919", print_mark=False)
    data2 = build_f0401_data(
        donate, [_line("帳篷", 1, "1000", "1000")], order_id="S1-7", tax_rate=Decimal("0.05")
    )
    assert data2["NPOBAN"] == "919"
    assert "CarrierType" not in data2


def test_f0401_discounted_line_uses_effective_unit_price() -> None:
    """折扣行：Amount＝實收小計；UnitPrice＝小計÷數量（Amount 為權威、兩者一致）。"""
    inv = _invoice(net=Decimal(857), tax=Decimal(43), total=Decimal(900))
    data = build_f0401_data(
        inv,
        [_line("帳篷", 2, "500", "900")],  # 原價 500×2、折 100 → 小計 900
        order_id="S1-7",
        tax_rate=Decimal("0.05"),
    )
    item = data["ProductItem"][0]
    assert item["Quantity"] == 2
    assert item["Amount"] == "900"
    assert item["UnitPrice"] == "450"
    assert data["SalesAmount"] == 900


def test_f0401_rejects_line_total_mismatch_with_invoice_total() -> None:
    """Σ小計 ≠ 發票總額 → 程式錯誤，拒建 payload（不可送出對不上的發票）。"""
    inv = _invoice(total=Decimal(1000), net=Decimal(952), tax=Decimal(48))
    with pytest.raises(ValueError):
        build_f0401_data(
            inv,
            [_line("帳篷", 1, "600", "600")],
            order_id="S1-7",
            tax_rate=Decimal("0.05"),
        )


def test_f0501_data_is_array_of_cancel_numbers() -> None:
    assert build_f0501_data("AB00001111") == [{"CancelInvoiceNumber": "AB00001111"}]


def test_invoice_query_data_by_order() -> None:
    assert build_invoice_query_data(order_id="S1-7") == {"type": "order", "order_id": "S1-7"}


class _RecordingTransport:
    """記錄送出的 form、回放 canned 回應（測試替身）。"""

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        self.calls.append((url, form))
        return self.response


async def test_client_posts_signed_form() -> None:
    transport = _RecordingTransport({"code": 0, "msg": "", "invoice_number": "AB00001111"})
    client = AmegoClient(
        seller_tax_id="12345678",
        app_key="sHeq7t8G1wiQvhAuIM27",
        transport=transport,
        base_url="https://invoice-api.amego.tw",
        now=lambda: datetime.fromtimestamp(1700000000, tz=UTC),
    )
    resp = await client.call("/json/f0501", build_f0501_data("AB00001111"))
    assert resp["code"] == 0
    url, form = transport.calls[0]
    assert url == "https://invoice-api.amego.tw/json/f0501"
    assert form["invoice"] == "12345678"
    assert form["time"] == "1700000000"
    data = form["data"]
    assert json.loads(data) == [{"CancelInvoiceNumber": "AB00001111"}]
    assert form["sign"] == hashlib.md5(
        f"{data}1700000000sHeq7t8G1wiQvhAuIM27".encode()
    ).hexdigest()


async def test_client_requires_credentials() -> None:
    transport = _RecordingTransport({"code": 0, "msg": ""})
    with pytest.raises(AmegoNotConfigured):
        AmegoClient(
            seller_tax_id="",
            app_key="key",
            transport=transport,
            base_url="https://invoice-api.amego.tw",
        )
    with pytest.raises(AmegoNotConfigured):
        AmegoClient(
            seller_tax_id="12345678",
            app_key="  ",
            transport=transport,
            base_url="https://invoice-api.amego.tw",
        )
