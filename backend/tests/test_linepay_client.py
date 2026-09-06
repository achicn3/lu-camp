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
    a = linepay_order_id(store_id=1, idempotency_key="pos-abc-123", amount=Decimal("100"))
    b = linepay_order_id(store_id=1, idempotency_key="pos-abc-123", amount=Decimal("100"))
    c = linepay_order_id(store_id=1, idempotency_key="pos-abc-999", amount=Decimal("100"))
    assert a == b  # 同鍵同額恆同號（重試不重扣的關鍵）
    assert a != c
    assert a.startswith("LP-1-") and len(a) <= 40
    # 不同店同鍵也不同號（多分店隔離）
    assert linepay_order_id(store_id=2, idempotency_key="pos-abc-123", amount=Decimal("100")) != a
    # 同鍵**不同金額**必得不同 orderId（Codex finding #2：杜絕以同鍵重送不同額誤重用）
    assert linepay_order_id(store_id=1, idempotency_key="pos-abc-123", amount=Decimal("200")) != a


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


# ── 載具自動帶入（2026-09-06 裁示）────────────────────────────────────────────
# 客人的 LINE Pay 綁了載具時，回應會在 info.merchantReference.affiliateCards[] 帶回來，
# cardType == MOBILE_CARRIER 那筆的 cardId 就是載具。有它就不必請客人再掃一次載具條碼。
def _pay_resp(cards: object) -> dict[str, object]:
    return {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {
            "transactionId": 2026071802368895010,
            "orderId": "LP-1-x",
            "merchantReference": {"affiliateCards": cards},
        },
    }


def test_parse_pay_result_extracts_mobile_carrier() -> None:
    r = parse_pay_result(
        _pay_resp([{"cardType": "MOBILE_CARRIER", "cardId": "/ABC1234"}])
    )
    assert r.mobile_carrier == "/ABC1234"


def test_parse_pay_result_picks_the_carrier_among_other_cards() -> None:
    """卡片可能有好幾張（會員卡、集點卡…），只認 MOBILE_CARRIER 那張。"""
    r = parse_pay_result(
        _pay_resp(
            [
                {"cardType": "MEMBERSHIP", "cardId": "M-999"},
                {"cardType": "MOBILE_CARRIER", "cardId": "/ABC1234"},
                {"cardType": "POINT", "cardId": "P-1"},
            ]
        )
    )
    assert r.mobile_carrier == "/ABC1234"


def test_parse_pay_result_without_carrier_is_none() -> None:
    """沒綁載具、或整個欄位不存在（文件未載明，不可假設一定回傳）→ None。"""
    assert parse_pay_result(_pay_resp([])).mobile_carrier is None
    only_member = _pay_resp([{"cardType": "MEMBERSHIP", "cardId": "M-1"}])
    assert parse_pay_result(only_member).mobile_carrier is None
    assert (
        parse_pay_result(
            {
                "returnCode": "0000",
                "returnMessage": "Success.",
                "info": {"transactionId": 1, "orderId": "x"},
            }
        ).mobile_carrier
        is None
    )


def test_parse_pay_result_accepts_carrier_with_or_without_leading_slash() -> None:
    """**兩種寫法都要收**，並一律正規化成帶斜線的標準格式。

    官方文件只說 `cardId` 是 String、說明「電子發票載具或會員卡ID」，**沒有規定格式**
    （2026-09-06 實際文件確認）。台灣手機條碼載具的標準寫法是 `/`＋7 碼，但 LINE Pay
    回的是哪一種無從得知。若硬性要求帶斜線，回不帶斜線時**每一個正確的載具都會被擋掉**，
    功能安靜地永遠不生效——這比擋錯一次嚴重得多。
    """
    def carrier(card_id: str) -> str | None:
        return parse_pay_result(
            _pay_resp([{"cardType": "MOBILE_CARRIER", "cardId": card_id}])
        ).mobile_carrier

    assert carrier("/ABC1234") == "/ABC1234"
    assert carrier("ABC1234") == "/ABC1234"
    assert carrier(" /ABC1234 ") == "/ABC1234"  # 前後空白也修掉（外部系統常見）


def test_parse_pay_result_rejects_malformed_carrier() -> None:
    """真的不成形的一律當成沒有——寧可讓店員自己掃，也不能把不合格的字送去開發票，
    那會開出一張載具錯誤的發票，事後得作廢重開。

    載具＝7 碼（數字／大寫英文／`+-.`）。小寫、長度不對、含非法字元都不收。
    """
    bad_ids: list[object] = [
        "/abc1234", "/ABC12345", "/ABC123", "ABC123", "ABC12345", "", "/", None, 12345,
    ]
    for bad in bad_ids:
        resp = _pay_resp([{"cardType": "MOBILE_CARRIER", "cardId": bad}])
        assert parse_pay_result(resp).mobile_carrier is None


def test_parse_pay_result_survives_junk_shapes() -> None:
    """欄位形狀不如預期時不得炸——付款已經成功了，解析載具失敗不該讓交易看起來失敗。"""
    junk_shapes: list[object] = [
        "not-a-list", {"a": 1}, [None], ["x"], [{"cardType": "MOBILE_CARRIER"}],
    ]
    for junk in junk_shapes:
        assert parse_pay_result(_pay_resp(junk)).mobile_carrier is None
    weird: dict[str, object] = {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {"transactionId": 1, "merchantReference": "not-a-dict"},
    }
    assert parse_pay_result(weird).mobile_carrier is None
