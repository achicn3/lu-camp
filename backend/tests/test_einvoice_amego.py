"""Amego 光貿 API 客戶端與 payload builder 單元測試（docs/24；純函式、無 DB）。

規格來源：docs/24-amego-einvoice.md（api_doc 2026-06-10 版）。
- 簽章：sign = md5(data JSON 字串 + time + App Key)。
- f0401 金額（含稅品項）：SalesAmount=Σ含稅小計；B2C TaxAmount=0；
  B2B TaxAmount = Sales − Round(Sales/1.05)、SalesAmount −= TaxAmount
  （與 split_tax_inclusive 同式）。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from app.modules.einvoice.amego import (
    AmegoClient,
    amego_order_id,
    build_f0401_data,
    build_f0501_data,
    build_invoice_query_data,
    parse_f0401_success,
    parse_query_allowance_exists,
    parse_query_invoice_voided,
    parse_query_issued,
    sign_form,
)
from app.modules.einvoice.models import Invoice
from app.modules.sales.models import SaleLine
from app.shared.enums import InvoiceStatus, InvoiceType, SaleLineKind, SaleLineType
from app.shared.exceptions import AmegoNotConfigured, AmegoTransportError, EInvoiceDropError


def _line(
    description: str,
    qty: int,
    unit_price: str,
    line_total: str,
    net_amount: str | None = None,
) -> SaleLine:
    """一般銷售行。`net_amount`（實付）才是發票品項金額的來源；省略時等於 line_total。"""
    return SaleLine(
        store_id=1,
        sale_id=7,
        line_type=SaleLineType.CATALOG,
        description=description,
        qty=qty,
        unit_price=Decimal(unit_price),
        line_total=Decimal(line_total),
        net_amount=Decimal(net_amount if net_amount is not None else line_total),
    )


def _gift_line(description: str, qty: int, retail: str) -> SaleLine:
    """贈品行：成交 0 元、留原價。發票品項必須排除它。"""
    return SaleLine(
        store_id=1,
        sale_id=7,
        line_type=SaleLineType.CATALOG,
        description=description,
        qty=qty,
        unit_price=Decimal(0),
        line_total=Decimal(0),
        net_amount=Decimal(0),
        line_kind=SaleLineKind.GIFT,
        original_unit_price=Decimal(retail),
    )


def _invoice(**overrides: object) -> Invoice:
    base: dict[str, object] = {
        "store_id": 1,
        "sale_id": 7,
        "invoice_type": InvoiceType.B2C,
        "net": Decimal(952),
        "tax": Decimal(48),
        "total": Decimal(1000),
        "tax_rate": Decimal("0.05"),
        "status": InvoiceStatus.PENDING,
        "donate_mark": False,
        "print_mark": True,
    }
    base.update(overrides)
    return Invoice(**base)


def test_sign_form_md5_of_data_time_key() -> None:
    data = '{"OrderId":"S1-7"}'
    expected = hashlib.md5(f"{data}1700000000unit-test-app-key".encode()).hexdigest()
    assert sign_form(data, 1700000000, "unit-test-app-key") == expected


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
    item = cast("list[dict[str, object]]", data["ProductItem"])[0]
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
    )
    assert data["BuyerIdentifier"] == "12345678"
    assert data["BuyerName"] == "測試環境有限公司"
    assert data["SalesAmount"] == 952
    assert data["TaxAmount"] == 48
    assert data["TotalAmount"] == 1000


def test_f0401_carrier_and_donation_fields() -> None:
    carrier = _invoice(carrier_type="3J0002", carrier_id="/ABC+123", print_mark=False)
    data = build_f0401_data(
        carrier, [_line("帳篷", 1, "1000", "1000")], order_id="S1-7"
    )
    assert data["CarrierType"] == "3J0002"
    assert data["CarrierId1"] == "/ABC+123"
    assert data["CarrierId2"] == "/ABC+123"
    assert "NPOBAN" not in data

    donate = _invoice(donate_mark=True, npoban="919", print_mark=False)
    data2 = build_f0401_data(
        donate, [_line("帳篷", 1, "1000", "1000")], order_id="S1-7"
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
    )
    item = cast("list[dict[str, object]]", data["ProductItem"])[0]
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
        )


def test_f0401_uses_net_amount_so_manual_discounts_reach_the_invoice() -> None:
    """臨時折扣落在 net_amount：發票品項若讀 line_total，Σ 就會超出發票總額而永遠送不出。"""
    inv = _invoice(net=Decimal(762), tax=Decimal(38), total=Decimal(800))
    data = build_f0401_data(
        inv,
        [_line("帳篷", 2, "500", "1000", net_amount="800")],
        order_id="S1-7",
    )
    item = cast("list[dict[str, object]]", data["ProductItem"])[0]
    assert item["Amount"] == "800"
    assert item["UnitPrice"] == "400"
    assert data["SalesAmount"] == 800


def test_f0401_excludes_gift_lines_and_still_balances() -> None:
    """贈品實付 0：排除後 Σ 仍等於發票總額，也不必假設平台接受 0 元品項行。"""
    inv = _invoice(net=Decimal(476), tax=Decimal(24), total=Decimal(500))
    data = build_f0401_data(
        inv,
        [_line("露營燈", 1, "500", "500"), _gift_line("小物", 1, "120")],
        order_id="S1-7",
    )
    items = cast("list[dict[str, object]]", data["ProductItem"])
    assert [item["Description"] for item in items] == ["露營燈"]
    assert data["SalesAmount"] == 500


def test_f0401_rejects_a_gift_only_invoice() -> None:
    """整單都是贈品就不該有發票（總額 0）；真的走到這裡是程式錯誤，拒建 payload。"""
    inv = _invoice(net=Decimal(476), tax=Decimal(24), total=Decimal(500))
    with pytest.raises(ValueError, match="沒有品項行"):
        build_f0401_data(inv, [_gift_line("小物", 1, "120")], order_id="S1-7")


def test_f0501_data_is_array_of_cancel_numbers() -> None:
    assert build_f0501_data("AB00001111") == [{"CancelInvoiceNumber": "AB00001111"}]


def test_invoice_query_data_by_order() -> None:
    assert build_invoice_query_data(order_id="S1-7") == {"type": "order", "order_id": "S1-7"}


_F0401_RESP = {
    "code": 0,
    "msg": "",
    "invoice_number": "AB00001111",
    "invoice_time": 1783766130,
    "random_number": "5975",
    "barcode": "11507AB000011115975",
    "qrcode_left": "L",
    "qrcode_right": "R",
}


def test_parse_f0401_rejects_bool_or_implausible_time() -> None:
    """invoice_time 為 JSON bool（int 子類）或不合理小值 → 拒收（Codex 第三輪）。"""
    for bad in (True, False, 100, "1783766130", None):
        with pytest.raises(AmegoTransportError):
            parse_f0401_success({**_F0401_RESP, "invoice_time": bad})
    result = parse_f0401_success(dict(_F0401_RESP))
    assert result.invoice_no == "AB00001111"


def test_parse_query_three_states() -> None:
    """查詢三態：成功回欄位；**明確查無**（整數非 0）回 None；曖昧一律拋（不得當查無重送）。"""
    found = {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_number": "AB00001111",
            "invoice_date": "20260711",
            "invoice_time": "12:34:56",
            "random_number": "5975",
            "total_amount": 1050,
            "invoice_status": 99,
            "create_date": _epoch_now(),
        },
    }
    result = parse_query_issued(found, expect_total=Decimal("1050"), expect_not_before=_recent())
    assert result is not None and result.barcode_text is None  # 查詢不回條碼內容
    assert (
        parse_query_issued(
            {"code": 71, "msg": "查無資料"},
            expect_total=Decimal("1050"),
            expect_not_before=_recent(),
        )
        is None
    )  # 官方查無碼
    ambiguous_responses: tuple[dict[str, object], ...] = (
        {"msg": "??"},
        {"code": "0", "msg": ""},
        {"code": True, "msg": ""},
        {"code": False, "msg": ""},
        {"code": 0, "msg": ""},  # code=0 卻缺 data
        {"code": 0, "data": {"invoice_number": "bad"}},
        {"code": 51, "msg": "該發票超過查詢期限"},  # 非查無錯誤碼：不得當查無（Codex 第六輪）
        {"code": 9001, "msg": "簽章錯誤"},
    )
    for ambiguous in ambiguous_responses:
        with pytest.raises(AmegoTransportError):
            parse_query_issued(ambiguous, expect_total=Decimal("1050"), expect_not_before=_recent())


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
        app_key="unit-test-app-key",
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
        f"{data}1700000000unit-test-app-key".encode()
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


def _recent() -> datetime:
    """本訊息誕生時點（測試中視為剛剛）。"""
    return datetime.now(tz=UTC) - timedelta(seconds=5)


def _epoch_now() -> int:
    return int(datetime.now(tz=UTC).timestamp())


def test_parse_query_issued_verifies_amount_identity() -> None:
    """對帳查到的紀錄必須**確實是本筆**：金額不符即拒（不得補記成功）。

    `order_id` 僅由 (store_id, sale_id) 推導，資料庫還原造成 id 倒退時會與平台上的
    歷史紀錄重號；只憑「查得到」就判定已開立，會把從未送出的 F0401 記成成功。
    """
    resp: dict[str, dict[str, object]] = {
        "data": {
            "invoice_number": "AB00001111",
            "invoice_date": "20260711",
            "invoice_time": "12:34:56",
            "random_number": "5975",
            "total_amount": 1500,
            "invoice_status": 99,
            "order_id": "S1-9001",
            "create_date": _epoch_now(),
        },
    }
    full: dict[str, object] = {"code": 0, "msg": "", **resp}
    ours = parse_query_issued(full, expect_total=Decimal("1500"), expect_not_before=_recent())
    assert ours is not None and ours.invoice_no == "AB00001111"

    # 別人的（或還原前的）紀錄：金額對不上 → 結果不可信，維持待對帳
    with pytest.raises(AmegoTransportError):
        parse_query_issued(full, expect_total=Decimal("270"), expect_not_before=_recent())

    # 平台沒回金額 → 無從驗證身分，同樣不可判定成功
    data_no_amount = {k: v for k, v in resp["data"].items() if k != "total_amount"}
    no_amount: dict[str, object] = {"code": 0, "msg": "", "data": data_no_amount}
    with pytest.raises(AmegoTransportError):
        parse_query_issued(no_amount, expect_total=Decimal("1500"), expect_not_before=_recent())

    # 查無仍是查無（可重送），與金額驗證無關
    assert (
        parse_query_issued(
            {"code": 71, "msg": "查無資料"},
            expect_total=Decimal("1500"),
            expect_not_before=_recent(),
        )
        is None
    )


def test_parse_query_invoice_voided_verifies_amount_identity() -> None:
    """F0501 對帳：字軌查到的發票金額須與本地一致，否則不可據以補記已作廢。"""
    voided = {
        "code": 0,
        "msg": "",
        "data": {"invoice_type": "C0501", "total_amount": 1500, "invoice_status": 99},
    }
    assert parse_query_invoice_voided(voided, expect_total=Decimal("1500")) is True
    with pytest.raises(AmegoTransportError):
        parse_query_invoice_voided(voided, expect_total=Decimal("270"))


def test_parse_query_allowance_exists_verifies_identity() -> None:
    """G0401 對帳：折讓單號同樣會跨還原重號，須比對原發票號與金額。"""
    resp = {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_type": "D0401",
            "total_amount": 476,  # 平台的折讓 total_amount 是未稅
            "tax_amount": 24,
            "invoice_status": 99,
            "create_date": _epoch_now(),
            "product_item": [{"original_invoice_number": "ZA10029234"}],
        },
    }
    assert (
        parse_query_allowance_exists(
            resp,
            expect_original_invoice_no="ZA10029234",
            expect_net=Decimal("476"),
            expect_tax=Decimal("24"),
            expect_not_before=_recent(),
        )
        is True
    )
    # 撞號：查到的是別張原發票的折讓
    with pytest.raises(AmegoTransportError):
        parse_query_allowance_exists(
            resp,
            expect_original_invoice_no="ZA10018786",
            expect_net=Decimal("476"),
            expect_tax=Decimal("24"),
            expect_not_before=_recent(),
        )
    # 金額不符
    with pytest.raises(AmegoTransportError):
        parse_query_allowance_exists(
            resp,
            expect_original_invoice_no="ZA10029234",
            expect_net=Decimal("190"),
            expect_tax=Decimal("10"),
            expect_not_before=_recent(),
        )
    # 明確查無仍可重送
    assert (
        parse_query_allowance_exists(
            {"code": 71, "msg": "查無資料"},
            expect_original_invoice_no="ZA10029234",
            expect_net=Decimal("476"),
            expect_tax=Decimal("24"),
            expect_not_before=_recent(),
        )
        is False
    )


def test_frozen_payload_identity_extractors_fail_closed() -> None:
    """對帳基準取自**凍結 payload**（我們實際送出的內容），讀不出即拒絕上送。

    寧可卡住等人工，也不可在無法驗證身分的情況下把平台上的別筆紀錄當成本筆。
    """
    from app.modules.einvoice.service import _payload_allowance_identity, _payload_total

    assert _payload_total([{"TotalAmount": 1050}]) == Decimal("1050")
    net, tax, original = _payload_allowance_identity(
        [
            {
                "TotalAmount": 1000,
                "TaxAmount": 50,
                "ProductItem": [{"OriginalInvoiceNumber": "AB00001111"}],
            }
        ]
    )
    assert (net, tax, original) == (Decimal("1000"), Decimal("50"), "AB00001111")

    bad_totals: tuple[object, ...] = (
        [{}],
        [{"TotalAmount": True}],
        [{"TotalAmount": "x"}],
        "?",
        [],
    )
    for bad_total in bad_totals:
        with pytest.raises(EInvoiceDropError):
            _payload_total(bad_total)

    bad_allowances: tuple[object, ...] = (
        [{"TotalAmount": 1000, "TaxAmount": 50}],  # 缺 ProductItem
        [{"TotalAmount": 1000, "TaxAmount": 50, "ProductItem": [{}]}],  # 缺原發票字軌
        [{"TaxAmount": 50, "ProductItem": [{"OriginalInvoiceNumber": "AB00001111"}]}],  # 缺未稅
    )
    for bad_allowance in bad_allowances:
        with pytest.raises(EInvoiceDropError):
            _payload_allowance_identity(bad_allowance)


def test_parse_query_invoice_voided_accepts_pending_void_in_wait() -> None:
    """平台受理但尚在處理的作廢：頂層仍 C0401、待作廢掛在 `wait[]`。

    形狀取自對真 Amego 測試環境的實測（送出 f0501 後立即查詢）：
    `invoice_type=C0401, invoice_status=1, wait=[{"invoice_type":"C0501", ...}]`。
    只看頂層會回 False → 對已受理的作廢再送一次 F0501，被拒後記 FAILED、
    發票卡在 VOID_PENDING。
    """
    pending = {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_type": "C0401",
            "invoice_status": 1,
            "cancel_date": 0,
            "total_amount": 1050,
            "wait": [{"invoice_type": "C0501", "create_date": _epoch_now()}],
        },
    }
    assert parse_query_invoice_voided(pending, expect_total=Decimal("1050")) is True
    # wait[] 內若不是作廢（例如空的），仍照頂層判斷為「仍開立、可送 F0501」
    still_open = {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_type": "C0401",
            "invoice_status": 99,
            "total_amount": 1050,
            "wait": [],
        },
    }
    assert parse_query_invoice_voided(still_open, expect_total=Decimal("1050")) is False


def test_allowance_original_invoice_check_fails_closed_on_unknown_items() -> None:
    """product_item 含非物件/缺字軌時不得被靜默略過（否則撞號折讓會被誤記成功）。"""
    def _resp(items: object) -> dict[str, object]:
        return {
            "code": 0,
            "msg": "",
            "data": {
                "invoice_type": "D0401",
                "invoice_status": 99,
                "total_amount": 476,
                "tax_amount": 24,
                "create_date": _epoch_now(),
                "product_item": items,
            },
        }

    ok = _resp([{"original_invoice_number": "ZA10029234"}])
    assert (
        parse_query_allowance_exists(
            ok,
            expect_original_invoice_no="ZA10029234",
            expect_net=Decimal("476"),
            expect_tax=Decimal("24"),
            expect_not_before=_recent(),
        )
        is True
    )
    for bad in (
        [{"original_invoice_number": "ZA10029234"}, None],  # 混入 null → 不得放行
        [{"original_invoice_number": "ZA10029234"}, {}],  # 缺字軌
        [{"original_invoice_number": "bad"}],  # 字軌格式不合法
    ):
        with pytest.raises(AmegoTransportError):
            parse_query_allowance_exists(
                _resp(bad),
                expect_original_invoice_no="ZA10029234",
                expect_net=Decimal("476"),
                expect_tax=Decimal("24"),
                expect_not_before=_recent(),
            )


def test_create_date_bounds_never_escape_as_non_amego_error() -> None:
    """超大 epoch 不得逃逸成 OverflowError（會變 500 且 last_error 空白）。"""
    def _resp(create_date: object) -> dict[str, object]:
        return {
            "code": 0,
            "msg": "",
            "data": {
                "invoice_number": "AB00001111",
                "invoice_date": "20260711",
                "invoice_time": "12:34:56",
                "random_number": "5975",
                "total_amount": 1050,
                "invoice_status": 99,
                "create_date": create_date,
            },
        }

    now = datetime.now(tz=UTC)
    for bad in (10**20, -(10**20), int((now + timedelta(days=1)).timestamp())):
        with pytest.raises(AmegoTransportError):
            parse_query_issued(_resp(bad), expect_total=Decimal("1050"), expect_not_before=now)
    # 容忍值內（早 60 秒）仍應通過
    ok = parse_query_issued(
        _resp(int((now - timedelta(seconds=60)).timestamp())),
        expect_total=Decimal("1050"),
        expect_not_before=now,
    )
    assert ok is not None


def test_pending_void_never_bypasses_identity_and_status_checks() -> None:
    """wait[] 判讀**必須在身分／狀態驗證之後**，否則等於在身分驗證上開後門。

    上一輪把 wait 檢查放最前面，導致金額不符的撞號紀錄只要掛著待作廢就被判定已作廢。
    """
    def _resp(**over: object) -> dict[str, object]:
        data: dict[str, object] = {
            "invoice_type": "C0401",
            "invoice_status": 1,
            "total_amount": 1050,
            "wait": [{"invoice_type": "C0501", "create_date": _epoch_now()}],
        }
        data.update(over)
        return {"code": 0, "msg": "", "data": data}

    assert parse_query_invoice_voided(_resp(), expect_total=Decimal("1050")) is True
    # 金額不符（撞號）→ 即便掛著待作廢也不得判定已作廢
    with pytest.raises(AmegoTransportError):
        parse_query_invoice_voided(_resp(), expect_total=Decimal("270"))
    # 平台錯誤態
    with pytest.raises(AmegoTransportError):
        parse_query_invoice_voided(_resp(invoice_status=91), expect_total=Decimal("1050"))
    # wait 形狀不明 → **不可**當成「沒有待作廢」而放行重送
    for bad_wait in ([None], "?", [{"create_date": 1}]):
        with pytest.raises(AmegoTransportError):
            parse_query_invoice_voided(_resp(wait=bad_wait), expect_total=Decimal("1050"))


def test_platform_error_status_is_never_treated_as_applied() -> None:
    """invoice_status=91（平台錯誤）與未知值不得驅動 UPLOADED/ISSUED/VOID/ALLOWANCE。"""
    issued = {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_number": "AB00001111",
            "invoice_date": "20260711",
            "invoice_time": "12:34:56",
            "random_number": "5975",
            "total_amount": 1050,
            "create_date": _epoch_now(),
            "invoice_status": 91,
        },
    }
    with pytest.raises(AmegoTransportError):
        parse_query_issued(issued, expect_total=Decimal("1050"), expect_not_before=_recent())

    allowance: dict[str, object] = {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_type": "D0401",
            "total_amount": 476,
            "tax_amount": 24,
            "create_date": _epoch_now(),
            "invoice_status": 91,
            "product_item": [{"original_invoice_number": "ZA10029234"}],
        },
    }
    with pytest.raises(AmegoTransportError):
        parse_query_allowance_exists(
            allowance,
            expect_original_invoice_no="ZA10029234",
            expect_net=Decimal("476"),
            expect_tax=Decimal("24"),
            expect_not_before=_recent(),
        )
    # 折讓同時掛著待作廢 → 狀態矛盾，同樣阻擋
    contradictory = {
        "code": 0,
        "msg": "",
        "data": {
            **allowance["data"],  # type: ignore[dict-item]
            "invoice_status": 99,
            "wait": [{"invoice_type": "D0501"}],
        },
    }
    with pytest.raises(AmegoTransportError):
        parse_query_allowance_exists(
            contradictory,
            expect_original_invoice_no="ZA10029234",
            expect_net=Decimal("476"),
            expect_tax=Decimal("24"),
            expect_not_before=_recent(),
        )


def test_f0401_success_rejects_absurd_invoice_time() -> None:
    """f0401 的 invoice_time 也要擋上界，否則 fromtimestamp 溢位會變 500、last_error 空白。"""
    base = {"code": 0, "invoice_number": "AB00001111", "random_number": "5975"}
    for bad in (10**20, int((datetime.now(tz=UTC) + timedelta(days=1)).timestamp())):
        with pytest.raises(AmegoTransportError):
            parse_f0401_success({**base, "invoice_time": bad})
