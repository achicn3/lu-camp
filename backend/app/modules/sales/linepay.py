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
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import uuid4

import httpx

from app.shared.exceptions import LinePayNotConfigured, LinePayTransportError

_CURRENCY = "TWD"
# 讀取逾時：官方要求 pay 至少 40 秒、check／refund 至少 20 秒（Offline API v4，2026-09-11 查閱）。
# 共用一個值取兩者較大者即可同時滿足，不必為每種操作分別設定。短於下限的代價是：本來會成功
# 的慢回應被當成逾時，平白變成「結果不明」而鎖單（原本是 20 秒，稽核 F03）。
LINEPAY_READ_TIMEOUT_SECONDS = 40.0
_PAY_PATH = "/v4/payments/oneTimeKeys/pay"
RETURN_CODE_SUCCESS = "0000"
RETURN_CODE_ALREADY_REFUNDED = "1165"  # refund：平台已退款（重試冪等，視為成功）

# pay 的「確定拒付」白名單：請求在扣款前就被平台擋下，可以放心請客人換方式或重掃。
# 依 Offline API v4「結果程式碼」表（2026-09-11 查閱）逐一挑出。
#
# **刻意用白名單而非黑名單**：不在這裡的一律當「結果未確認」、鎖單查原單——包含
# 官方表上沒有的碼與空值。反過來列「哪些是處理中」的話，漏列一個就又會叫店員重收。
# 以下這些**不可**加進來，它們都代表前次可能已經扣款：
#   1145 付款進行中、1152 有相同交易歷史、1172 同訂單號已有交易、1198 請求重複、
#   1199／9000 內部錯誤（無法判斷）。
DEFINITIVE_PAY_REJECT_CODES: frozenset[str] = frozenset(
    {
        "1101",  # 該用戶不是 LINE Pay 用戶
        "1102",  # 該用戶目前無法使用 LINE Pay 交易
        "1104",  # 商店尚未註冊為合作商店
        "1105",  # 該合作商店目前無法使用 LINE Pay
        "1106",  # 請求標頭訊息有錯誤
        "1110",  # 該信用卡無法正常使用
        "1124",  # 金額訊息有誤
        "1133",  # 無效的付款碼（oneTimeKey）
        "1141",  # 帳戶狀態有問題
        "1142",  # 餘額不足
        "1153",  # 付款請求金額和請款金額不同
        "1159",  # 無付款請求訊息
        "1178",  # 合作商店不支援該貨幣
        "1183",  # 付款金額低於最低金額
        "1184",  # 付款金額高於最高金額
        "2020",  # EPI 預授權階段未能預留限額
        "2021",  # 超出受限用戶的支付限額
        "2022",  # 超出使用者的支付限額
        "2023",  # 超出個別使用者在該商戶的限額
        "2024",  # 超出商戶可收款限額
        "2101",  # 參數錯誤
        "2102",  # JSON 數據格式錯誤
        "2103",  # 輸入了不允許的參數
        "2104",  # 無效的請求
    }
)
_CHECK_STATUS_COMPLETE = "COMPLETE"


def linepay_order_id(*, store_id: int, idempotency_key: str, amount: Decimal) -> str:
    """OrderId（唯一、確定性、長度受限）：由 (store, 冪等鍵, 金額) 導出。

    以**冪等鍵**（非 sale.id）導出——rollback/retry 後 sale.id 會變、冪等鍵不變，同一次結帳恆得
    同 orderId，重試先 check(orderId) 即可避免重複扣款。**金額納入摘要**（Codex adversarial
    finding #2）：同鍵但不同金額必得不同 orderId，杜絕「回應遺失後以同鍵重送不同金額」把前次
    NT$1000 收款誤當成本次 NT$2000 之付款重用。以 SHA-256 摘要截斷確保長度與字元安全。
    """
    digest = hashlib.sha256(f"{idempotency_key}:{int(amount)}".encode()).hexdigest()[:32]
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
    amount: Decimal | None = None  # pay／check 的 Σ info.payInfo[].amount（核對實付金額）
    # 客人綁在 LINE Pay 上的手機條碼載具（見 _mobile_carrier）。沒有就是 None。
    mobile_carrier: str | None = None

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


# 手機條碼載具的**碼身**：7 碼（數字/大寫英文/+-.）。標準寫法帶前導 `/`
# （與 SaleInvoiceInfoRequest 同一套），但 LINE Pay 回哪一種無從得知——見 _mobile_carrier。
_CARRIER_BODY_RE = re.compile(r"^[0-9A-Z+\-.]{7}$")
_CARD_TYPE_MOBILE_CARRIER = "MOBILE_CARRIER"


def _mobile_carrier(info: object) -> str | None:
    """從 `info.merchantReference.affiliateCards[]` 取出客人綁定的載具。

    客人的 LINE Pay 綁了載具時，那張卡會以 `cardType == "MOBILE_CARRIER"` 出現，
    `cardId` 就是載具號碼——有它就不必請客人再掃一次載具條碼（2026-09-06 裁示）。

    依官方文件（Offline API v4「付款請求」回應，2026-09-06 查閱）：
    - `merchantReference` 標示 **TW only**，且「欲使用此欄位，請聯絡 LINE Pay 負責人」
      ——**未申請開通就不會回傳**，且僅在「該交易用戶符合該合作商店載具或會員卡類型」
      時才包含。所以取不到是常態，不是異常。
    - `cardType == "MOBILE_CARRIER"` 時，載具資訊在 `cardId`；**其他類型由各合作商店
      自行定義**，故只能精確比對這個字串，不可做模糊匹配。

    **格式一律正規化**：文件只說 `cardId` 是 String，**沒有規定格式**。台灣手機條碼載具的
    標準寫法是 `/`＋7 碼，但 LINE Pay 回的是哪一種無從得知——若硬性要求帶斜線，回不帶
    斜線時**每一個正確的載具都會被擋掉**，功能安靜地永遠不生效。故兩種都收、統一補上斜線。

    真的不成形的（小寫、長度不對、非法字元）仍一律回 None：寧可讓店員自己掃，也不能把
    不合格的字送去開發票——那會開出一張載具錯誤的發票，事後得作廢重開。
    付款本身已經成功了，解析失敗絕不能讓交易看起來失敗。
    """
    if not isinstance(info, dict):
        return None
    reference = info.get("merchantReference")
    if not isinstance(reference, dict):
        return None
    cards = reference.get("affiliateCards")
    if not isinstance(cards, list):
        return None
    for card in cards:
        if not isinstance(card, dict):
            continue
        if card.get("cardType") != _CARD_TYPE_MOBILE_CARRIER:
            continue
        card_id = card.get("cardId")
        if not isinstance(card_id, str):
            continue
        body = card_id.strip().removeprefix("/")
        if _CARRIER_BODY_RE.match(body):
            return f"/{body}"
    return None


def _pay_info_total(info: object) -> Decimal | None:
    """Σ info.payInfo[].amount；沒有 payInfo 回 None（官方文件未標為必要，不可當成缺陷）。

    一筆付款可能拆成多種方式（例如餘額＋點數），要比對的是總額而不是第一項。
    """
    if not isinstance(info, dict):
        return None
    pay_info = info.get("payInfo")
    if not isinstance(pay_info, list):
        return None
    total = Decimal(0)
    for entry in pay_info:
        if isinstance(entry, dict) and entry.get("amount") is not None:
            total += Decimal(str(entry["amount"]))
    return total


def parse_pay_result(resp: dict[str, object]) -> LinePayResult:
    """oneTimeKeys/pay 回應解析。成功（0000）必含 info.transactionId，缺則視為傳輸不可信。

    `amount` 為平台回報的實付總額（payInfo 合計），呼叫端用來核對是否等於請款金額。
    """
    code = str(resp.get("returnCode") or "")
    message = str(resp.get("returnMessage") or "")
    info = resp.get("info")
    tx = _transaction_id_str(info)
    if code == RETURN_CODE_SUCCESS and tx is None:
        raise LinePayTransportError("LINE Pay pay 回 0000 但缺 transactionId（結果不可信）")
    return LinePayResult(
        return_code=code,
        return_message=message,
        transaction_id=tx,
        status=None,
        raw=resp,
        amount=_pay_info_total(info),
        mobile_carrier=_mobile_carrier(info),
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
    amount = _pay_info_total(info)
    return LinePayResult(
        return_code=code,
        return_message=message,
        transaction_id=tx,
        status=status,
        raw=resp,
        amount=amount,
    )


def parse_refund_result(resp: dict[str, object]) -> LinePayResult:
    """refund 回應解析。0000＝成功（info.refundTransactionId，非 transactionId）、
    1165＝已退款（冪等成功）；不要求 transactionId（退款回應無此欄）。"""
    code = str(resp.get("returnCode") or "")
    message = str(resp.get("returnMessage") or "")
    return LinePayResult(
        return_code=code, return_message=message, transaction_id=None, status=None, raw=resp
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
            async with httpx.AsyncClient(timeout=LINEPAY_READ_TIMEOUT_SECONDS) as client:
                resp = await client.request(
                    method, url, headers=headers, content=body if body else None
                )
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise LinePayTransportError(f"LINE Pay API 呼叫失敗：{exc.__class__.__name__}") from exc
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
        return parse_refund_result(resp)


def linepay_client_from_config() -> LinePayClient | None:
    """依 config 建 LINE Pay 客戶端（憑證來自環境變數、不入 repo）。未設定 → None，
    由呼叫端對帶 LINE_PAY 的結帳/退款 fail-closed 拒絕（不留無付款/未退款單）。共用工廠。"""
    from app.core.config import get_settings

    cfg = get_settings()
    if not cfg.linepay_channel_id.strip() or not cfg.linepay_channel_secret.strip():
        return None
    return LinePayClient(
        channel_id=cfg.linepay_channel_id,
        channel_secret=cfg.linepay_channel_secret,
        base_url=cfg.linepay_api_base,
        transport=HttpxLinePayTransport(),
    )
