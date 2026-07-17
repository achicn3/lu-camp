"""LINE Pay Offline API v4 客戶端與 payload builder（docs/30）。

只做「店家掃客人 QR/條碼」收款（oneTimeKeys/pay，同步授權+請款）。已對真沙盒驗證
（見 docs/30 附錄）。此檔只含純函式（payload 組裝/簽章/回應解析）與薄客戶端（簽章＋
送出＋JSON 解析）；linepay_transactions 落庫與 create_sale 整合在 service 層。

認證（所有請求）——已實測接受：
- header `X-LINE-ChannelId`: Channel ID
- header `X-LINE-Authorization-Nonce`: UUID
- header `X-LINE-Authorization`:
  `base64( HMAC-SHA256( key=ChannelSecret, msg=ChannelSecret + apiPath + body + nonce ) )`
  （GET 以 queryString 取代 body；本店 check 無 query → 空字串）

端點（host = sandbox-api-pay.line.me / 正式 api-pay.line.me）：
- 收款：POST /v4/payments/oneTimeKeys/pay
- 查詢：GET  /v4/payments/orders/{orderId}/check
- 退款：POST /v4/payments/orders/{orderId}/refund   ← 吃 orderId（非交易號；實測修正）

金額一律整數元（§6）。transactionId 為 64-bit 長整數：Python int 無失真，一律以字串保存。
"""

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

import httpx

from app.shared.exceptions import LinePayNotConfigured, LinePayTransportError

_CURRENCY = "TWD"
_HTTP_TIMEOUT_SECONDS = 20.0
_PAY_PATH = "/v4/payments/oneTimeKeys/pay"
RETURN_CODE_SUCCESS = "0000"
_CHECK_STATUS_COMPLETE = "COMPLETE"


def linepay_order_id(*, store_id: int, idempotency_key: str) -> str:
    """OrderId（唯一、確定性、長度受限）：由 (store, 冪等鍵) 導出。

    以**冪等鍵**（非 sale.id）導出——rollback/retry 後 sale.id 會變、冪等鍵不變，
    同一次結帳恆得同 orderId，重試先 check(orderId) 即可避免重複扣款。以 SHA-256 摘要
    截斷確保長度與字元安全（冪等鍵可能含任意字元/過長）。
    """
    digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
    return f"LP-{store_id}-{digest}"


def check_path(order_id: str) -> str:
    return f"/v4/payments/orders/{order_id}/check"


def refund_path(order_id: str) -> str:
    return f"/v4/payments/orders/{order_id}/refund"


def sign_auth(*, channel_secret: str, api_path: str, body: str, nonce: str) -> str:
    """X-LINE-Authorization：base64(HMAC-SHA256(key=Secret, msg=Secret+apiPath+body+nonce))。

    GET 請求以 queryString 取代 body（本店 check 無 query → body=""）。
    """
    message = f"{channel_secret}{api_path}{body}{nonce}".encode()
    digest = hmac.new(channel_secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_packages(*, amount: Decimal, product_name: str) -> list[dict[str, object]]:
    """pay body 的 packages（必填）。

    以**單一彙總商品**表示整筆消費（price=amount、quantity=1）——packages 僅供 LINE App
    顯示，單品彙總可保證 package.amount 與逐品加總一致，免逐行捨入湊不齊被平台退（2101）。
    """
    total = int(amount)
    return [
        {
            "id": "pkg-1",
            "amount": total,
            "name": product_name,
            "products": [{"name": product_name, "quantity": 1, "price": total}],
        }
    ]


def build_pay_body(
    *,
    order_id: str,
    amount: Decimal,
    one_time_key: str,
    packages: list[dict[str, object]],
) -> dict[str, object]:
    """POST /v4/payments/oneTimeKeys/pay 的 body（實測 packages 必填）。"""
    return {
        "amount": int(amount),
        "currency": _CURRENCY,
        "orderId": order_id,
        "oneTimeKey": one_time_key,
        "packages": packages,
    }


@dataclass(frozen=True)
class LinePayResult:
    """pay/check 回應解析結果。transactionId 以字串保存（64-bit，勿落 JS Number 邊界）。"""

    return_code: str
    return_message: str
    transaction_id: str | None
    status: str | None  # check 的 info.status（COMPLETE/FAIL/CANCEL/AUTH_READY）；pay 無
    raw: dict[str, object]  # 原始回應（對帳存證，落 linepay_transactions.raw_response）

    @property
    def is_success(self) -> bool:
        return self.return_code == RETURN_CODE_SUCCESS

    @property
    def is_complete(self) -> bool:
        """check 專用：平台已請款完成。"""
        return self.is_success and self.status == _CHECK_STATUS_COMPLETE


def _transaction_id_str(info: object) -> str | None:
    """從 info.transactionId 取字串（Python int 無失真，str() 即精確）。"""
    if not isinstance(info, dict):
        return None
    tx = info.get("transactionId")
    if tx is None:
        return None
    return str(tx)


def parse_pay_result(resp: dict[str, object]) -> LinePayResult:
    """oneTimeKeys/pay 回應解析。成功（0000）必含 info.transactionId，缺則視為傳輸不可信。"""
    code = str(resp.get("returnCode") or "")
    message = str(resp.get("returnMessage") or "")
    info = resp.get("info")
    tx = _transaction_id_str(info)
    if code == RETURN_CODE_SUCCESS and tx is None:
        raise LinePayTransportError("LINE Pay pay 回 0000 但缺 transactionId（結果不可信）")
    return LinePayResult(
        return_code=code, return_message=message, transaction_id=tx, status=None, raw=resp
    )


def parse_check_result(resp: dict[str, object]) -> LinePayResult:
    """orders/{orderId}/check 回應解析：回 status（COMPLETE/FAIL/CANCEL/AUTH_READY）。"""
    code = str(resp.get("returnCode") or "")
    message = str(resp.get("returnMessage") or "")
    info = resp.get("info")
    tx = _transaction_id_str(info)
    status = None
    if isinstance(info, dict) and info.get("status") is not None:
        status = str(info.get("status"))
    return LinePayResult(
        return_code=code, return_message=message, transaction_id=tx, status=status, raw=resp
    )


class LinePayTransport(Protocol):
    """傳輸替身介面：送已簽章的 HTTP 請求、回 JSON dict（測試以錄放替身實作）。"""

    async def send(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> dict[str, object]: ...


class HttpxLinePayTransport:
    """真傳輸：httpx 送出；網路/逾時/非 JSON → LinePayTransportError（結果未知）。"""

    async def send(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.request(
                    method, url, headers=headers, content=body if body else None
                )
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise LinePayTransportError(
                f"LINE Pay API 呼叫失敗：{exc.__class__.__name__}"
            ) from exc
        except ValueError as exc:
            raise LinePayTransportError("LINE Pay API 回應非 JSON") from exc
        if not isinstance(payload, dict):
            raise LinePayTransportError("LINE Pay API 回應非 JSON 物件")
        return payload


class LinePayClient:
    """薄客戶端：body JSON 序列化 → HMAC 簽章 → 送出。

    `nonce_factory` 可注入（測試固定 nonce）。body JSON 以緊湊分隔序列化——**簽章覆蓋的
    正是送出的那個字串**（簽章與傳輸用同一份，不得各自再序列化）。
    """

    def __init__(
        self,
        *,
        channel_id: str,
        channel_secret: str,
        base_url: str,
        transport: LinePayTransport,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        if not channel_id.strip() or not channel_secret.strip():
            raise LinePayNotConfigured(
                "LINE Pay 憑證未設定（Channel ID/Secret），不可呼叫 Offline API"
            )
        self._channel_id = channel_id
        self._channel_secret = channel_secret
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._nonce = nonce_factory if nonce_factory is not None else lambda: str(uuid4())

    def _headers(self, api_path: str, body: str) -> dict[str, str]:
        nonce = self._nonce()
        return {
            "Content-Type": "application/json",
            "X-LINE-ChannelId": self._channel_id,
            "X-LINE-Authorization-Nonce": nonce,
            "X-LINE-Authorization": sign_auth(
                channel_secret=self._channel_secret,
                api_path=api_path,
                body=body,
                nonce=nonce,
            ),
        }

    async def pay(
        self, *, order_id: str, amount: Decimal, one_time_key: str, product_name: str
    ) -> LinePayResult:
        """同步授權+請款。回 LinePayResult（呼叫端據 is_success/fail-closed 判讀）。"""
        packages = build_packages(amount=amount, product_name=product_name)
        body_obj = build_pay_body(
            order_id=order_id, amount=amount, one_time_key=one_time_key, packages=packages
        )
        body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
        resp = await self._transport.send(
            "POST", f"{self._base_url}{_PAY_PATH}", self._headers(_PAY_PATH, body), body
        )
        return parse_pay_result(resp)

    async def check(self, *, order_id: str) -> LinePayResult:
        """以 orderId 查訂單狀態（重試/逾時對帳用）。GET 無 body → 簽章 body 為空字串。"""
        path = check_path(order_id)
        resp = await self._transport.send(
            "GET", f"{self._base_url}{path}", self._headers(path, ""), None
        )
        return parse_check_result(resp)

    async def refund(self, *, order_id: str, refund_amount: Decimal) -> LinePayResult:
        """退款（以 orderId；退貨/作廢反轉）。回 LinePayResult（0000＝成功、1165＝已退款）。"""
        path = refund_path(order_id)
        body_obj: dict[str, object] = {"refundAmount": int(refund_amount)}
        body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
        resp = await self._transport.send(
            "POST", f"{self._base_url}{path}", self._headers(path, body), body
        )
        return parse_pay_result(resp)
