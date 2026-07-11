"""Amego 光貿電子發票 API 客戶端與 payload builder（docs/24）。

傳輸協定（api_doc 2026-06-10 版）：POST `application/x-www-form-urlencoded`，欄位
`invoice`（賣方統編）、`data`（API 參數 JSON 字串）、`time`（Unix 秒，±60s）、
`sign`＝md5(data JSON 字串 + time + App Key)。回應 JSON：`code`（0＝成功）、`msg`、
各端點另有資料欄。**測試/正式同一 API 網址**，以統編＋App Key 區分環境。

此檔只含純函式（payload 組裝/簽章）與薄客戶端（簽章＋送出＋JSON 解析）；
佇列狀態機與發票欄位落庫在 `service.py`。金額一律 Decimal → 整數元（§6）。
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from app.core.money import round_ntd, split_tax_inclusive
from app.modules.einvoice.models import Invoice
from app.modules.sales.models import SaleLine
from app.shared.enums import InvoiceType
from app.shared.exceptions import AmegoNotConfigured, AmegoTransportError

# MIG 課稅別（doc：1 應稅／2 零稅率／3 免稅）。本店僅應稅品項。
_TAX_TYPE_TAXABLE = 1
# B2C 無統編時的制式買方統編（doc）。
_B2C_BUYER_IDENTIFIER = "0000000000"
_B2C_BUYER_NAME = "消費者"
_DESCRIPTION_MAX = 256
_HTTP_TIMEOUT_SECONDS = 15.0


def amego_order_id(*, store_id: int, sale_id: int) -> str:
    """OrderId（唯一、≤40 字）：由 (store, sale) 確定性導出——重試恆同號，
    Amego 端「OrderId 不可重複」即天然防同一銷售重複開立。"""
    return f"S{store_id}-{sale_id}"


def sign_form(data_json: str, timestamp: int, app_key: str) -> str:
    """sign = md5(data JSON 字串 + time + App Key)（doc 基本傳入參數）。

    md5 為 Amego 指定的簽章格式（非本系統的密碼學選型），usedforsecurity=False。
    """
    return hashlib.md5(
        f"{data_json}{timestamp}{app_key}".encode(), usedforsecurity=False
    ).hexdigest()


def _decimal_str(value: Decimal) -> str:
    """Decimal → 無指數、無尾零字串（"450"、"52.5"）；金額欄位以字串傳輸。"""
    text = format(value.normalize(), "f")
    return text


def build_f0401_data(
    invoice: Invoice,
    lines: list[SaleLine],
    *,
    order_id: str,
    tax_rate: Decimal,
) -> dict[str, object]:
    """組 f0401（開立發票）payload——含稅品項（DetailVat 預設 1）。

    金額規則（doc「含稅商品金額計算邏輯」）：SalesAmount = Σ 含稅小計；
    B2C（無統編）TaxAmount=0；B2B TaxAmount = Sales − Round(Sales/(1+rate))、
    SalesAmount −= TaxAmount——與本系統 `split_tax_inclusive` 同式，直接以發票的
    net/tax（結帳時同函式算出）為準。Σ小計必須等於發票總額，不等即程式錯誤拒送。
    """
    if not lines:
        raise ValueError("發票沒有品項行，不可送開立")
    line_sum = Decimal(0)
    items: list[dict[str, object]] = []
    for line in lines:
        if line.qty <= 0 or line.line_total < 0:
            raise ValueError(f"品項行不合法（qty={line.qty}, line_total={line.line_total}）")
        line_sum += Decimal(line.line_total)
        # Amount（實收小計）為權威；折扣行的 UnitPrice 以小計÷數量表示（兩者一致，
        # 避免平台以 Quantity×UnitPrice 驗算時對不上）。
        effective_unit = Decimal(line.line_total) / Decimal(line.qty)
        items.append(
            {
                "Description": line.description[:_DESCRIPTION_MAX],
                "Quantity": line.qty,
                "UnitPrice": _decimal_str(effective_unit),
                "Amount": _decimal_str(Decimal(line.line_total)),
                "TaxType": _TAX_TYPE_TAXABLE,
            }
        )
    total = Decimal(invoice.total)
    if line_sum != total:
        raise ValueError(f"品項小計合計 {line_sum} 不等於發票總額 {total}，拒送開立")

    if invoice.invoice_type is InvoiceType.B2B:
        if not invoice.buyer_tax_id:
            raise ValueError("B2B 發票缺買方統編")
        net, tax = split_tax_inclusive(total, tax_rate)
        buyer_identifier = invoice.buyer_tax_id
        buyer_name = invoice.buyer_name or invoice.buyer_tax_id
        sales_amount, tax_amount = int(net), int(tax)
    else:
        buyer_identifier = _B2C_BUYER_IDENTIFIER
        buyer_name = invoice.buyer_name or _B2C_BUYER_NAME
        sales_amount, tax_amount = int(round_ntd(total)), 0

    data: dict[str, object] = {
        "OrderId": order_id,
        "BuyerIdentifier": buyer_identifier,
        "BuyerName": buyer_name,
        "ProductItem": items,
        "SalesAmount": sales_amount,
        "FreeTaxSalesAmount": 0,
        "ZeroTaxSalesAmount": 0,
        "TaxType": _TAX_TYPE_TAXABLE,
        "TaxRate": _decimal_str(tax_rate),
        "TaxAmount": tax_amount,
        "TotalAmount": sales_amount + tax_amount,
    }
    if invoice.carrier_type and invoice.carrier_id:
        data["CarrierType"] = invoice.carrier_type
        data["CarrierId1"] = invoice.carrier_id
        data["CarrierId2"] = invoice.carrier_id
    if invoice.donate_mark and invoice.npoban:
        data["NPOBAN"] = invoice.npoban
    return data


def build_f0501_data(invoice_number: str) -> list[dict[str, str]]:
    """f0501（作廢發票）payload：陣列，每元素一張 {CancelInvoiceNumber}。"""
    return [{"CancelInvoiceNumber": invoice_number}]


# 折讓單種類（doc）：114-01-01 起經雙方合意之退回/折讓，賣方應開立並依限上傳 → 恆用 2。
_ALLOWANCE_TYPE_SELLER = 2


def allowance_number(*, store_id: int, allowance_id: int) -> str:
    """自編折讓單號（唯一、≤16 字）：由 (store, allowance) 確定性導出。"""
    return f"L{store_id}-{allowance_id}"


def build_g0401_data(
    *,
    number: str,
    allowance_date: date,
    invoice: Invoice,
    net: Decimal,
    tax: Decimal,
) -> list[dict[str, object]]:
    """g0401（開立折讓）payload：陣列（每元素一張折讓）。

    品項/金額為**未稅**口徑（doc）：以折讓的 net/tax（開立時 split_tax_inclusive 算出）
    彙總為單行「銷貨退回折讓」——本系統折讓按退貨總額開立（§7 不變量 5），不逐品項。
    原發票必須已開立（number/date 已配）。
    """
    if not invoice.invoice_no or invoice.invoice_date is None:
        raise ValueError("原發票缺字軌/開立日，不可開立折讓")
    return [
        {
            "AllowanceNumber": number,
            "AllowanceDate": allowance_date.strftime("%Y%m%d"),
            "AllowanceType": _ALLOWANCE_TYPE_SELLER,
            "BuyerIdentifier": invoice.buyer_tax_id or _B2C_BUYER_IDENTIFIER,
            "BuyerName": invoice.buyer_name
            or (invoice.buyer_tax_id or _B2C_BUYER_NAME),
            "ProductItem": [
                {
                    "OriginalInvoiceNumber": invoice.invoice_no,
                    "OriginalInvoiceDate": int(invoice.invoice_date.strftime("%Y%m%d")),
                    "OriginalDescription": "銷貨退回折讓",
                    "Quantity": 1,
                    "UnitPrice": _decimal_str(net),
                    "Amount": _decimal_str(net),
                    "Tax": int(tax),
                    "TaxType": _TAX_TYPE_TAXABLE,
                }
            ],
            "TaxAmount": int(tax),
            "TotalAmount": int(net),
        }
    ]


def build_invoice_query_data(*, order_id: str) -> dict[str, str]:
    """invoice_query（發票查詢）payload：以訂單編號查（未知結果對帳復原用）。"""
    return {"type": "order", "order_id": order_id}


# 發票日期/時間以台灣時區呈現（f0401 回傳 invoice_time 為 Unix 秒）。
_TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_INVOICE_NO_RE = re.compile(r"^[A-Z]{2}\d{8}$")
_RANDOM_RE = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class AmegoIssueResult:
    """f0401 成功回應（或 invoice_query 復原）解析結果——寫回本地發票的欄位。

    barcode/qrcode 僅 f0401 回應才有（查詢不回傳）；缺者證明聯不可印。
    """

    invoice_no: str
    invoice_date: date
    invoice_time: str  # HH:MM:SS
    random_number: str
    barcode_text: str | None
    qrcode_left: str | None
    qrcode_right: str | None


def parse_f0401_success(resp: dict[str, object]) -> AmegoIssueResult:
    """驗證並解析 f0401 成功回應；欄位缺漏/格式不符 → AmegoTransportError（結果不可信，
    佇列維持已認領待對帳，不得寫入半套開立事實）。"""
    number = str(resp.get("invoice_number") or "")
    random_number = str(resp.get("random_number") or "")
    raw_time = resp.get("invoice_time")
    if not _INVOICE_NO_RE.match(number) or not _RANDOM_RE.match(random_number):
        raise AmegoTransportError("Amego f0401 回應欄位不合法（字軌/隨機碼）")
    if not isinstance(raw_time, int) or raw_time <= 0:
        raise AmegoTransportError("Amego f0401 回應欄位不合法（invoice_time）")
    issued_at = datetime.fromtimestamp(raw_time, tz=_TAIPEI_TZ)
    barcode = str(resp.get("barcode") or "") or None
    qr_left = str(resp.get("qrcode_left") or "") or None
    qr_right = str(resp.get("qrcode_right") or "") or None
    return AmegoIssueResult(
        invoice_no=number,
        invoice_date=issued_at.date(),
        invoice_time=issued_at.strftime("%H:%M:%S"),
        random_number=random_number,
        barcode_text=barcode,
        qrcode_left=qr_left,
        qrcode_right=qr_right,
    )


def parse_query_issued(resp: dict[str, object]) -> AmegoIssueResult | None:
    """invoice_query 回應 → 若平台已有此訂單的發票，回其開立欄位；查無/不完整 → None。

    查詢不回傳條碼/QR 內容 → 證明聯欄位為 None（復原的發票不可印證明聯）。
    """
    code = resp.get("code")
    if type(code) is not int or code != 0:  # bool 是 int 子類，JSON true/false 不得矇混
        return None
    data = resp.get("data")
    if not isinstance(data, dict):
        return None
    number = str(data.get("invoice_number") or "")
    random_number = str(data.get("random_number") or "")
    raw_date = str(data.get("invoice_date") or "")
    raw_time = str(data.get("invoice_time") or "")
    if not _INVOICE_NO_RE.match(number) or not _RANDOM_RE.match(random_number):
        return None
    try:
        issued_date = datetime.strptime(raw_date, "%Y%m%d").date()
        issued_time = datetime.strptime(raw_time, "%H:%M:%S").strftime("%H:%M:%S")
    except ValueError:
        return None
    return AmegoIssueResult(
        invoice_no=number,
        invoice_date=issued_date,
        invoice_time=issued_time,
        random_number=random_number,
        barcode_text=None,
        qrcode_left=None,
        qrcode_right=None,
    )


class AmegoTransport(Protocol):
    """傳輸替身介面：送 form、回 JSON dict（測試以錄放替身實作）。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]: ...


class HttpxAmegoTransport:
    """真傳輸：httpx POST x-www-form-urlencoded；網路/非 JSON 失敗 → AmegoTransportError。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, data=form)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            raise AmegoTransportError(f"Amego API 呼叫失敗：{exc.__class__.__name__}") from exc
        except ValueError as exc:
            raise AmegoTransportError("Amego API 回應非 JSON") from exc
        if not isinstance(payload, dict):
            raise AmegoTransportError("Amego API 回應非 JSON 物件")
        return payload


class AmegoClient:
    """薄客戶端：data JSON 序列化 → 簽章 → 送出。

    `now` 可注入（測試固定時間戳）；`data` JSON 以 ensure_ascii=False + 緊湊分隔
    序列化——簽章覆蓋的正是這個字串。
    """

    def __init__(
        self,
        *,
        seller_tax_id: str,
        app_key: str,
        transport: AmegoTransport,
        base_url: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not seller_tax_id.strip():
            raise AmegoNotConfigured("店家統編未設定（stores.tax_id），不可呼叫 Amego API")
        if not app_key.strip():
            raise AmegoNotConfigured("AMEGO_APP_KEY 未設定（環境變數），不可呼叫 Amego API")
        self._seller_tax_id = seller_tax_id
        self._app_key = app_key
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._now = now if now is not None else lambda: datetime.now(UTC)

    async def call(self, endpoint: str, data: object) -> dict[str, object]:
        """送一筆 API 請求，回解析後的 JSON dict（code/msg 由呼叫端判讀）。"""
        data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        timestamp = int(self._now().timestamp())
        form = {
            "invoice": self._seller_tax_id,
            "data": data_json,
            "time": str(timestamp),
            "sign": sign_form(data_json, timestamp, self._app_key),
        }
        return await self._transport.post_form(f"{self._base_url}{endpoint}", form)
