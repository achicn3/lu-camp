"""LINE Pay Offline v4 客戶端純邏輯測試（docs/30）：簽章、payload、回應解析、傳輸替身。

簽章向量以獨立 HMAC 計算交叉驗證（不重抄實作），確保與沙盒實測一致。
"""

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from app.modules.sales.linepay import (
    LinePayClient,
    LinePayTransport,
    build_packages,
    build_pay_body,
    check_path,
    linepay_order_id,
    parse_check_result,
    parse_pay_result,
    refund_path,
    sign_auth,
)
from app.shared.exceptions import LinePayNotConfigured, LinePayTransportError

SECRET = "9a652ad2e79be83f7979e4ea761747a9"
CHANNEL = "2010746859"


def test_order_id_stable_and_bounded() -> None:
    a = linepay_order_id(store_id=1, idempotency_key="pos-abc-123")
    b = linepay_order_id(store_id=1, idempotency_key="pos-abc-123")
    c = linepay_order_id(store_id=1, idempotency_key="pos-abc-999")
    assert a == b  # 同鍵恆同號（重試不重扣的關鍵）
    assert a != c
    assert a.startswith("LP-1-") and len(a) <= 40
    # 不同店同鍵也不同號（多分店隔離）
    assert linepay_order_id(store_id=2, idempotency_key="pos-abc-123") != a


def test_sign_auth_matches_independent_hmac() -> None:
    nonce = "fixed-nonce-uuid"
    path = "/v4/payments/oneTimeKeys/pay"
    body = '{"amount":250}'
    got = sign_auth(channel_secret=SECRET, api_path=path, body=body, nonce=nonce)
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), f"{SECRET}{path}{body}{nonce}".encode(), hashlib.sha256).digest()
    ).decode()
    assert got == expected


def test_sign_auth_get_uses_empty_body() -> None:
    # GET check 無 query → 簽章 body 為空字串
    nonce = "n"
    path = check_path("LP-1-deadbeef")
    got = sign_auth(channel_secret=SECRET, api_path=path, body="", nonce=nonce)
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), f"{SECRET}{path}{nonce}".encode(), hashlib.sha256).digest()
    ).decode()
    assert got == expected


def test_build_packages_single_aggregate() -> None:
    pkgs = build_packages(amount=Decimal(1350), product_name="門市消費")
    assert len(pkgs) == 1
    assert pkgs[0]["amount"] == 1350
    products = pkgs[0]["products"]
    assert isinstance(products, list)
    assert products[0]["price"] == 1350 and products[0]["quantity"] == 1


def test_build_pay_body_shape() -> None:
    body = build_pay_body(
        order_id="LP-1-x",
        amount=Decimal(250),
        one_time_key="OTK123",
        packages=build_packages(amount=Decimal(250), product_name="門市消費"),
    )
    assert body["amount"] == 250
    assert body["currency"] == "TWD"
    assert body["orderId"] == "LP-1-x"
    assert body["oneTimeKey"] == "OTK123"
    assert isinstance(body["packages"], list)


def test_parse_pay_result_success_keeps_big_transaction_id_as_string() -> None:
    # 19 位長整數：Python int 無失真，str() 精確（JS 才會被污染成 ...000）
    resp: dict[str, object] = {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {"transactionId": 2026071802368895010, "orderId": "LP-1-x"},
    }
    r = parse_pay_result(resp)
    assert r.is_success
    assert r.transaction_id == "2026071802368895010"


def test_parse_pay_result_failure_codes() -> None:
    assert parse_pay_result({"returnCode": "1133", "returnMessage": "invalid"}).is_success is False
    assert parse_pay_result({"returnCode": "2101", "returnMessage": "param"}).transaction_id is None


def test_parse_pay_result_success_without_tx_is_transport_error() -> None:
    with pytest.raises(LinePayTransportError):
        parse_pay_result({"returnCode": "0000", "returnMessage": "Success.", "info": {}})


def test_parse_check_result_status() -> None:
    resp: dict[str, object] = {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {"transactionId": 111, "status": "COMPLETE"},
    }
    r = parse_check_result(resp)
    assert r.is_complete
    assert r.transaction_id == "111"
    not_complete = parse_check_result(
        {"returnCode": "0000", "info": {"status": "AUTH_READY"}}
    )
    assert not_complete.is_success and not not_complete.is_complete


def test_refund_path_uses_order_id() -> None:
    assert refund_path("LP-1-x") == "/v4/payments/orders/LP-1-x/refund"


def test_client_requires_credentials() -> None:
    with pytest.raises(LinePayNotConfigured):
        LinePayClient(
            channel_id="", channel_secret=SECRET, base_url="https://x", transport=_FakeTransport()
        )
    with pytest.raises(LinePayNotConfigured):
        LinePayClient(
            channel_id=CHANNEL,
            channel_secret="  ",
            base_url="https://x",
            transport=_FakeTransport(),
        )


class _FakeTransport(LinePayTransport):
    """錄放替身：記下最後一次請求，回預設回應。"""

    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {
            "returnCode": "0000",
            "returnMessage": "ok",
            "info": {"transactionId": 5},
        }
        self.method = ""
        self.url = ""
        self.headers: dict[str, str] = {}
        self.body: str | None = None

    async def send(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> dict[str, object]:
        self.method, self.url, self.headers, self.body = method, url, headers, body
        return self.response


@pytest.mark.asyncio
async def test_client_pay_signs_exact_serialized_body() -> None:
    transport = _FakeTransport(
        {"returnCode": "0000", "returnMessage": "Success.", "info": {"transactionId": 999}}
    )
    client = LinePayClient(
        channel_id=CHANNEL,
        channel_secret=SECRET,
        base_url="https://sandbox-api-pay.line.me",
        transport=transport,
        nonce_factory=lambda: "fixed-nonce",
    )
    result = await client.pay(
        order_id="LP-1-abc", amount=Decimal(250), one_time_key="OTK", product_name="門市消費"
    )
    assert result.transaction_id == "999"
    sent_body = transport.body
    assert sent_body is not None
    # 送出的 body 必須是可解析 JSON，且簽章覆蓋的正是這個字串
    parsed = json.loads(sent_body)
    assert parsed["orderId"] == "LP-1-abc"
    expected_sig = sign_auth(
        channel_secret=SECRET,
        api_path="/v4/payments/oneTimeKeys/pay",
        body=sent_body,
        nonce="fixed-nonce",
    )
    assert transport.headers["X-LINE-Authorization"] == expected_sig
    assert transport.headers["X-LINE-ChannelId"] == CHANNEL
    assert transport.url.endswith("/v4/payments/oneTimeKeys/pay")


@pytest.mark.asyncio
async def test_client_check_is_get_with_empty_body_signature() -> None:
    transport = _FakeTransport(
        {"returnCode": "0000", "info": {"transactionId": 1, "status": "COMPLETE"}}
    )
    client = LinePayClient(
        channel_id=CHANNEL,
        channel_secret=SECRET,
        base_url="https://x",
        transport=transport,
        nonce_factory=lambda: "n",
    )
    r = await client.check(order_id="LP-1-abc")
    assert r.is_complete
    assert transport.method == "GET"
    assert transport.body is None
    path = check_path("LP-1-abc")
    assert transport.headers["X-LINE-Authorization"] == sign_auth(
        channel_secret=SECRET, api_path=path, body="", nonce="n"
    )


@pytest.mark.asyncio
async def test_client_refund_posts_amount_to_order_path() -> None:
    transport = _FakeTransport(
        {"returnCode": "0000", "info": {"transactionId": 7}}
    )
    client = LinePayClient(
        channel_id=CHANNEL,
        channel_secret=SECRET,
        base_url="https://x",
        transport=transport,
        nonce_factory=lambda: "n",
    )
    r = await client.refund(order_id="LP-1-abc", refund_amount=Decimal(250))
    assert r.is_success
    assert transport.method == "POST"
    assert transport.url.endswith("/v4/payments/orders/LP-1-abc/refund")
    assert transport.body is not None
    assert json.loads(transport.body)["refundAmount"] == 250
