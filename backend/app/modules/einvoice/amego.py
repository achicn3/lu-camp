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

from app.core.money import round_ntd
from app.modules.einvoice.models import Invoice
from app.modules.sales.models import SaleLine
from app.shared.enums import InvoiceType, SaleLineKind
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
) -> dict[str, object]:
    """組 f0401（開立發票）payload——含稅品項（DetailVat 預設 1）。

    金額規則（doc「含稅商品金額計算邏輯」）：SalesAmount = Σ 含稅小計；
    B2C（無統編）TaxAmount=0；B2B 直接以發票**落地快照** net/tax（結帳時
    split_tax_inclusive 算出、DB CHECK net+tax=total 守護）為準——不得在上送時
    以活 settings 稅率重算（結帳後改稅率會讓申報與本地帳不一致，Codex 第九輪）。
    TaxRate 同用發票的 tax_rate 快照。Σ小計必須等於發票總額，不等即程式錯誤拒送。
    """
    # 贈品行排除於發票品項之外：它實收 0，排除後 Σ 仍等於發票總額，
    # 且不必假設平台接受 0 元品項行（本 repo 對此無任何佐證）。
    billable = [line for line in lines if line.line_kind is not SaleLineKind.GIFT]
    if not billable:
        raise ValueError("發票沒有品項行，不可送開立")
    line_sum = Decimal(0)
    items: list[dict[str, object]] = []
    for line in billable:
        # 品項金額認**實付**（net_amount）：Σ net_amount == sale.total == invoice.total。
        # line_total 只是活動折後的牌價小計，臨時折扣不在其中。
        if line.qty <= 0 or line.net_amount < 0:
            raise ValueError(f"品項行不合法（qty={line.qty}, net_amount={line.net_amount}）")
        line_sum += Decimal(line.net_amount)
        # Amount（實收小計）為權威；折扣行的 UnitPrice 以小計÷數量表示（兩者一致，
        # 避免平台以 Quantity×UnitPrice 驗算時對不上）。
        effective_unit = Decimal(line.net_amount) / Decimal(line.qty)
        items.append(
            {
                "Description": line.description[:_DESCRIPTION_MAX],
                "Quantity": line.qty,
                "UnitPrice": _decimal_str(effective_unit),
                "Amount": _decimal_str(Decimal(line.net_amount)),
                "TaxType": _TAX_TYPE_TAXABLE,
            }
        )
    total = Decimal(invoice.total)
    if line_sum != total:
        raise ValueError(f"品項小計合計 {line_sum} 不等於發票總額 {total}，拒送開立")

    if invoice.invoice_type is InvoiceType.B2B:
        if not invoice.buyer_tax_id:
            raise ValueError("B2B 發票缺買方統編")
        buyer_identifier = invoice.buyer_tax_id
        buyer_name = invoice.buyer_name or invoice.buyer_tax_id
        sales_amount, tax_amount = int(invoice.net), int(invoice.tax)
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
        "TaxRate": _decimal_str(Decimal(invoice.tax_rate)),
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
_MAX_DATABASE_INTEGER_ID = 2_147_483_647
_BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _base36(value: int) -> str:
    """Encode a non-negative integer without padding."""
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_BASE36_ALPHABET[remainder])
    return "".join(reversed(digits))


def allowance_number(*, store_id: int, allowance_id: int) -> str:
    """自編折讓單號（唯一、≤16 字）：由 (store, allowance) 確定性導出。

    短 ID 保留既有可讀格式，避免已建立但尚待重送的折讓在部署後換號。超過
    光貿 16 字限制時，將兩個 PostgreSQL signed-int ID 無碰撞地封裝後轉 base36；
    ``LX`` 前綴與短格式的 ``L<數字>-<數字>`` 命名空間互斥。
    """
    if not 0 < store_id <= _MAX_DATABASE_INTEGER_ID:
        raise ValueError("store_id 超出可編碼範圍")
    if not 0 < allowance_id <= _MAX_DATABASE_INTEGER_ID:
        raise ValueError("allowance_id 超出可編碼範圍")

    readable = f"L{store_id}-{allowance_id}"
    if len(readable) <= 16:
        return readable

    packed = (store_id << 32) | allowance_id
    compact = f"LX{_base36(packed)}"
    if len(compact) > 16:  # defensive: signed-int IDs currently fit in 15 chars
        raise ValueError("折讓單號超過光貿 16 字限制")
    return compact


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
            "BuyerName": invoice.buyer_name or (invoice.buyer_tax_id or _B2C_BUYER_NAME),
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


# EPSON TM-T82III 的機型代碼（Amego「機型支援功能表」；官方表列支援正本與**補印**、BIG5）。
# 換機種要改這裡——代碼錯了 Amego 會產出另一台機器的指令，印出來是亂碼。
AMEGO_PRINTER_TYPE_TM_T82III = 3
# 列印格式：1＝發票正本、2＝發票補印。
AMEGO_PRINT_TYPE_ORIGINAL = 1
AMEGO_PRINT_TYPE_REPRINT = 2


def build_invoice_print_data(
    *, order_id: str, printer_type: int, reprint: bool = True
) -> dict[str, str | int]:
    """invoice_print（發票列印）payload：**以訂單編號查**，回傳可直送的 ESC/POS。

    **為什麼用 order_id 而不是發票號碼**：`OrderId` 由 `(store, sale)` 確定性導出，
    只要那筆銷售在就一定算得出來；而發票號碼正是「送出成功但回應遺失」時弄丟的東西。
    用它當鍵等於要求先有你沒有的東西。

    **為什麼要有這支**：證明聯的二維條碼含一段以財政部金鑰加密的驗證資訊，
    加值中心才有那把鑰匙，本地推算不出來。`invoice_query` 只回隨機碼、不回條碼內容
    （已對真平台實測），所以補印只能靠這支由 Amego 產生整張版面。

    平台限制：**只能查 180 天內的發票**；0 元發票不回傳列印內容。
    """
    return {
        "type": "order",
        "order_id": order_id,
        "printer_type": printer_type,
        "print_invoice_type": (
            AMEGO_PRINT_TYPE_REPRINT if reprint else AMEGO_PRINT_TYPE_ORIGINAL
        ),
    }


def parse_invoice_print(resp: dict[str, object]) -> str:
    """取出 base64 的 ESC/POS；平台未回內容一律拋錯，不回空字串讓呼叫端誤印空白。"""
    code = resp.get("code")
    if code != 0:
        raise AmegoTransportError(f"invoice_print 回錯誤碼 {code}：{resp.get('msg')}")
    data = resp.get("data")
    payload = data.get("base64_data") if isinstance(data, dict) else None
    if not isinstance(payload, str) or payload == "":
        raise AmegoTransportError(
            "invoice_print 未回傳列印內容（0 元發票或該機型不支援；不可據此列印空白）"
        )
    return payload


def build_invoice_query_data(*, order_id: str) -> dict[str, str]:
    """invoice_query（發票查詢）payload：以訂單編號查（未知結果對帳復原用）。"""
    return {"type": "order", "order_id": order_id}


def build_invoice_query_by_number_data(*, invoice_number: str) -> dict[str, str]:
    """invoice_query：以字軌號碼查（F0501 作廢前確認平台作廢態）。"""
    return {"type": "invoice", "invoice_number": invoice_number}


def build_allowance_query_data(*, number: str) -> dict[str, str]:
    """allowance_query（折讓查詢）payload：以自編折讓單號查（G0401 對帳用）。"""
    return {"allowance_number": number}


# 發票日期/時間以台灣時區呈現（f0401 回傳 invoice_time 為 Unix 秒）。
_TAIPEI_TZ = ZoneInfo("Asia/Taipei")
# **必須 [0-9] + fullmatch**：Python 的 `\d` 接受 Unicode 數字（全形 `ＺＡ１２３４５６７８`、
# 阿拉伯-印度 `ZA١٢٣٤٥٦٧٨` 都會過），而 `$` 還允許尾端換行。這裡吃的是**平台回傳**、
# 會原樣寫進 invoices.invoice_no，PostgreSQL 唯一索引又把它們與 ASCII 版視為不同號碼
# → 重號與對帳失真（Codex 對抗審查第二輪 high）。
_INVOICE_NO_RE = re.compile(r"[A-Z]{2}[0-9]{8}")
_RANDOM_RE = re.compile(r"[0-9]{4}")
# 開立時間戳合理下界（2020-09）：擋 JSON bool/epoch 附近的胡說值被記成開立時間。
_MIN_PLAUSIBLE_UNIX = 1_600_000_000
# 官方文件明載的「查無資料」錯誤碼（invoice_query/invoice_file/invoice_print 通用）。
_QUERY_NOT_FOUND_CODE = 71


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
    if not _INVOICE_NO_RE.fullmatch(number) or not _RANDOM_RE.fullmatch(random_number):
        raise AmegoTransportError("Amego f0401 回應欄位不合法（字軌/隨機碼）")
    # type() 嚴格檢查：Python bool 是 int 子類，JSON true 不得被記成 epoch 附近的開立時間。
    # 上界同樣要擋：只驗下界時 invoice_time=10**20 會通過守衛，再由 fromtimestamp 拋
    # OverflowError（非 AmegoTransportError）→ 請求變 500 且 last_error 空白（Codex 第三輪）。
    if (
        type(raw_time) is not int
        or raw_time < _MIN_PLAUSIBLE_UNIX
        or raw_time > int(datetime.now(tz=UTC).timestamp()) + _CLOCK_TOLERANCE_SECONDS
    ):
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


# 平台與本機時鐘容忍（秒）：Amego 簽章要求客戶端時間誤差在 ±60 秒內，故 120 秒已涵蓋
# 時鐘誤差與秒級截斷。**不可放寬到數分鐘**——那會把「還原後立刻重新營業」這個最危險的
# 撞號窗口整個放行。
_CLOCK_TOLERANCE_SECONDS = 120


def _assert_created_after(data: dict[str, object], not_before: datetime, *, ctx: str) -> None:
    """平台紀錄的建檔時間不得早於這則稅務訊息誕生的時點，否則**不可能是本筆**。

    撞號的本質是「查到的是還原點之前建立的舊紀錄」，而本佇列列必然是還原之後才建立的。
    金額相同的歷史交易在固定售價的門市並不罕見，故金額只是碰撞篩選、不是身分證明；
    這條時間下限才是把同額撞號擋下來的關鍵。

    `not_before` 應取**佇列列的 created_at**（非本次認領的 dropped_at）：同一列的前一次
    嘗試可能已在更早時間送出，那正是對帳要救的 crash 窗口，用認領時間會誤擋合法復原。
    """
    raw = data.get("create_date")
    if type(raw) is not int:  # bool 是 int 子類，但 JSON true/false 不會是合理秒數
        raise AmegoTransportError(
            f"{ctx} 回應缺 create_date 或型別不明（無從確認是否為本筆，待對帳）"
        )
    # **先在整數 epoch 域比較**：datetime.fromtimestamp() 對超大整數會拋 OverflowError/OSError，
    # 那不是 AmegoTransportError，會讓請求變成 500 且 last_error 一片空白（Codex 第二輪）。
    lower = int(not_before.timestamp()) - _CLOCK_TOLERANCE_SECONDS
    upper = int(datetime.now(tz=UTC).timestamp()) + _CLOCK_TOLERANCE_SECONDS
    if raw < lower:
        raise AmegoTransportError(
            f"{ctx} 查到的紀錄建檔時間 {raw} 早於本訊息的 {int(not_before.timestamp())}"
            "——該紀錄是還原前的舊資料（識別碼重號），待人工對帳"
        )
    if raw > upper:
        raise AmegoTransportError(
            f"{ctx} 回應的 create_date {raw} 超前本機時鐘逾容忍值（回應不可信），待對帳"
        )


# 平台 invoice_status（docs/24 §2）：1 待處理、2 上傳中、3 已上傳、31 處理中、
# 32 處理完成／待確認、91 錯誤、99 完成。**91 是錯誤，不得當成已套用**；未知值同樣不可信。
# 其餘「在途」狀態視同已受理——與直接送出得到 code=0 的語意一致（都只代表平台收下）。
_ACCEPTED_PLATFORM_STATUS = frozenset({1, 2, 3, 31, 32, 99})


def _assert_platform_status_ok(data: dict[str, object], *, ctx: str) -> None:
    """平台紀錄本身必須不是錯誤態，否則不可據以判定「已套用」。"""
    status = data.get("invoice_status")
    if type(status) is not int:
        raise AmegoTransportError(f"{ctx} 回應缺 invoice_status 或型別不明（不可信），待對帳")
    if status not in _ACCEPTED_PLATFORM_STATUS:
        raise AmegoTransportError(
            f"{ctx} 平台紀錄 invoice_status={status}（錯誤或未知狀態，不得視為已套用），待對帳"
        )


def _platform_amount(data: dict[str, object], field: str, *, ctx: str) -> Decimal:
    """取平台回傳的金額欄（整數元）。缺欄或型別不明 → 無從驗證身分，一律拋。

    bool 是 int 子類，JSON true/false 不得矇混成金額。
    """
    raw = data.get(field)
    if type(raw) is int:
        return Decimal(raw)
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return Decimal(raw.strip())
    raise AmegoTransportError(f"{ctx} 回應缺 {field} 或型別不明（無從確認是否為本筆，待對帳）")


def _assert_same_record(actual: Decimal, expected: Decimal, *, ctx: str, label: str) -> None:
    """對帳身分驗證：查到的紀錄金額須與本地一致，否則**不是本筆**。

    `order_id`／折讓單號只由 (store_id, sale_id/allowance_id) 推導，資料庫自備份還原
    造成 id 倒退時會與平台既有紀錄重號。只憑「查得到」就補記成功，會把從未送出的
    F0401/F0501/G0401 記為已上傳——稅務帳目就此失真。金額不符一律拋，維持待人工對帳
    （寧可卡住也不可誤記，亦不可貿然重送而產生重複稅務憑證）。
    """
    if actual != expected:
        raise AmegoTransportError(
            f"{ctx} 查到的{label} {actual} 與本地 {expected} 不符"
            "——該紀錄可能不是本筆（識別碼重號），待人工對帳"
        )


def parse_query_issued(
    resp: dict[str, object], *, expect_total: Decimal, expect_not_before: datetime
) -> AmegoIssueResult | None:
    """invoice_query 回應三態（Codex 第三輪）：

    - 成功（code=0 且欄位齊備）→ 開立欄位（條碼/QR 查詢不回傳 → None，證明聯不可印）。
    - **明確查無**（code=71「查無資料」——官方文件明載的查無碼）→ None，呼叫端才可重送。
    - 其餘（code 缺/bool/型別不明、code=0 但欄位缺漏、或**其他錯誤碼**——授權失敗/
      限流/超過查詢期限等都不證明平台沒開過）→ AmegoTransportError：
      結果不明**不得視為查無而重送**，維持待對帳（Codex 第六輪）。
    """
    code = resp.get("code")
    if type(code) is not int:  # bool 是 int 子類，JSON true/false 不得矇混
        raise AmegoTransportError("invoice_query 回應 code 型別不明（結果不可信，待對帳）")
    if code == _QUERY_NOT_FOUND_CODE:
        return None  # 平台明確回答「查無資料」
    if code != 0:
        raise AmegoTransportError(f"invoice_query 回錯誤碼 {code}（非查無，不得據以重送；待對帳）")
    data = resp.get("data")
    if not isinstance(data, dict):
        raise AmegoTransportError("invoice_query 回 code=0 但缺 data（結果不可信，待對帳）")
    number = str(data.get("invoice_number") or "")
    random_number = str(data.get("random_number") or "")
    raw_date = str(data.get("invoice_date") or "")
    raw_time = str(data.get("invoice_time") or "")
    if not _INVOICE_NO_RE.fullmatch(number) or not _RANDOM_RE.fullmatch(random_number):
        raise AmegoTransportError("invoice_query 回應欄位不合法（字軌/隨機碼），待對帳")
    try:
        issued_date = datetime.strptime(raw_date, "%Y%m%d").date()
        issued_time = datetime.strptime(raw_time, "%H:%M:%S").strftime("%H:%M:%S")
    except ValueError as exc:
        raise AmegoTransportError("invoice_query 回應欄位不合法（日期/時間），待對帳") from exc
    _assert_same_record(
        _platform_amount(data, "total_amount", ctx="invoice_query"),
        expect_total,
        ctx="invoice_query",
        label="含稅總額",
    )
    _assert_created_after(data, expect_not_before, ctx="invoice_query")
    _assert_platform_status_ok(data, ctx="invoice_query")
    # **頂層動作型別必須是開立**：C0501（已作廢）／C0701（已註銷）代表原發票已不成立，
    # 補記成 ISSUED 會讓本地與平台直接矛盾（重現：兩者原本都會被當成已開立）。
    # 這種「查得到但狀態相斥」屬曖昧，一律待人工對帳。
    found_type = str(data.get("invoice_type") or "")
    if found_type not in _INVOICE_TYPE_ISSUED:
        raise AmegoTransportError(
            f"invoice_query 查到的紀錄型別為「{found_type}」（非開立態），不得補記為已開立，待對帳"
        )
    # 平台掛著任何待處理動作時**一律 fail closed**：先前只驗形狀就放行，理由是「作廢由
    # 本地 VOID 佇列處理」——但沒有任何地方證明本地真的有那條後續流程。本分支處理的正是
    # 備份還原情境：DB 可能保有已認領的 F0401、卻沒有備份後才發生的作廢／折讓佇列列，
    # 於是本地被標成 ISSUED 而平台其實正在作廢，且永遠不會被修正（Codex 第五輪）。
    # 「查無＋已在作廢流程」另有既有的空窗作廢分支處理；此處查得到卻狀態未定，交人工。
    pending_kinds = {
        str(w.get("invoice_type") or "") for w in _wait_entries(data, ctx="invoice_query")
    }
    if pending_kinds:
        raise AmegoTransportError(
            f"invoice_query 查到的紀錄另掛待處理的 {sorted(pending_kinds)}"
            "——本地是否有對應後續流程無從確認，待人工對帳"
        )
    return AmegoIssueResult(
        invoice_no=number,
        invoice_date=issued_date,
        invoice_time=issued_time,
        random_number=random_number,
        barcode_text=None,
        qrcode_left=None,
        qrcode_right=None,
    )


# invoice_query data.invoice_type 的存證訊息語意（doc）：開立/作廢。
_INVOICE_TYPE_ISSUED = frozenset({"C0401", "A0401"})
_INVOICE_TYPE_VOIDED = frozenset({"C0501", "A0501"})
# allowance_query data.invoice_type：存證折讓開立/作廢。
_ALLOWANCE_TYPE_ISSUED = frozenset({"D0401", "B0401"})
_ALLOWANCE_TYPE_VOIDED = frozenset({"D0501", "B0501"})
# 註銷（沖銷重開）——與作廢同屬「原發票已不成立」的動作。
_INVOICE_TYPE_CANCELLED = frozenset({"C0701", "A0701"})
# `wait[]` 可能出現的已知待處理型別。**未知型別一律拋**：無法判斷它與本次動作是否相斥。
_KNOWN_WAIT_TYPES = (
    _INVOICE_TYPE_VOIDED
    | _INVOICE_TYPE_CANCELLED
    | _ALLOWANCE_TYPE_ISSUED
    | _ALLOWANCE_TYPE_VOIDED
)


def _wait_entries(data: dict[str, object], *, ctx: str) -> list[dict[str, object]]:
    """嚴格取出 `wait[]`。形狀不明一律拋——**不可當成「沒有待處理項目」**。

    寬鬆解讀會 fail open：wait 若是非 list 或含非 dict 元素而被當成空，作廢就會被重送。
    """
    if "wait" not in data:
        return []  # 欄位不存在＝該回應不帶待處理清單
    waiting = data["wait"]
    # 明示 null 與「沒有這個欄位」不同：實測平台無待處理時回 `[]`，出現 null 代表
    # 序列化異常，不可解讀成「沒有待處理項目」而放行相斥動作。
    if not isinstance(waiting, list):
        raise AmegoTransportError(f"{ctx} 的 wait 形狀不明（回應不可信），待對帳")
    entries: list[dict[str, object]] = []
    for item in waiting:
        if not isinstance(item, dict):
            raise AmegoTransportError(f"{ctx} 的 wait 含非物件元素（回應不可信），待對帳")
        kind = str(item.get("invoice_type") or "")
        if kind not in _KNOWN_WAIT_TYPES:
            raise AmegoTransportError(
                f"{ctx} 的 wait 含未知型別「{kind}」——無法判斷是否與本次動作相斥，待人工對帳"
            )
        entries.append(item)
    return entries


def _assert_no_pending_entries(
    data: dict[str, object], kinds: frozenset[str], *, ctx: str
) -> None:
    """狀態自相矛盾（例如折讓同時掛著待作廢）→ 阻擋，要求人工對帳。"""
    found = {str(w.get("invoice_type") or "") for w in _wait_entries(data, ctx=ctx)} & kinds
    if found:
        raise AmegoTransportError(
            f"{ctx} 查到的紀錄另掛待處理的 {sorted(found)}（狀態矛盾），待人工對帳"
        )


def parse_query_invoice_voided(resp: dict[str, object], *, expect_total: Decimal) -> bool:
    """invoice_query（以字軌查）→ 平台是否已作廢此發票（F0501 對帳，Codex 第七輪）。

    True＝已作廢（invoice_type C0501/A0501）→ 本地補記成功、不重送；
    False＝仍為開立態（C0401/A0401）→ 可送 F0501；
    其餘（查無 71——我們確信發票存在、錯誤碼、曖昧欄位）→ AmegoTransportError 待人工/重試。
    """
    code = resp.get("code")
    if type(code) is not int:
        raise AmegoTransportError("invoice_query 回應 code 型別不明（結果不可信，待對帳）")
    if code != 0:
        raise AmegoTransportError(
            f"invoice_query（作廢確認）回錯誤碼 {code}——查無/錯誤都不證明作廢態，待對帳"
        )
    data = resp.get("data")
    invoice_type = str(data.get("invoice_type") or "") if isinstance(data, dict) else ""
    if invoice_type in _INVOICE_TYPE_VOIDED or invoice_type in _INVOICE_TYPE_ISSUED:
        # **身分與狀態驗證一律先於任何「已套用」判定**（Codex 第三輪）：先前把 wait 判讀
        # 放在金額比對之前，導致金額不符的撞號紀錄只要掛著待作廢就被回 True，等於在剛修好的
        # 身分驗證上開後門。
        _ctx = "invoice_query（作廢確認）"
        assert isinstance(data, dict)  # invoice_type 非空即代表 data 是 dict
        _assert_same_record(
            _platform_amount(data, "total_amount", ctx=_ctx),
            expect_total,
            ctx=_ctx,
            label="含稅總額",
        )
        _assert_platform_status_ok(data, ctx=_ctx)
        # **平台受理但尚在處理的作廢，頂層仍是 C0401**，待作廢掛在 `wait[]`（對真 Amego
        # 實測：送出 f0501 後 invoice_type=C0401、status=1、wait=[{"invoice_type":"C0501"}]）。
        # 只看頂層會回 False → 對已受理的作廢再送一次 F0501，被拒後記 FAILED、發票卡在
        # VOID_PENDING，正是 crash 窗口最需要救的情境（Codex 第二輪）。
        # **先蒐集全部事實再決策**：早退會讓後面的衝突檢查被跳過——本輪重現過
        # `wait=[C0501, D0401]` 直接回 True，於是平台仍掛著相斥折讓，本地卻已把發票轉 VOID。
        pending = {str(w.get("invoice_type") or "") for w in _wait_entries(data, ctx=_ctx)}
        # 註銷（C0701/A0701）是**另一種動作**，不能當成本次 F0501 已被受理。
        conflicting = pending & (
            _ALLOWANCE_TYPE_ISSUED | _ALLOWANCE_TYPE_VOIDED | _INVOICE_TYPE_CANCELLED
        )
        if conflicting:
            raise AmegoTransportError(
                f"{_ctx} 查到的紀錄另掛待處理的 {sorted(conflicting)}"
                "——與本次作廢相斥，待人工對帳"
            )
        if pending & _INVOICE_TYPE_VOIDED:
            return True
    if invoice_type in _INVOICE_TYPE_VOIDED:
        return True
    if invoice_type in _INVOICE_TYPE_ISSUED:
        return False
    raise AmegoTransportError(
        f"invoice_query 回不明 invoice_type「{invoice_type}」（結果不可信，待對帳）"
    )


def _assert_allowance_original_invoice(data: dict[str, object], expected: str) -> None:
    """折讓明細的原發票號須全部指向本地那張原發票（否則是別筆折讓撞號）。"""
    items = data.get("product_item")
    if not isinstance(items, list) or not items:
        raise AmegoTransportError("allowance_query 缺 product_item（無從確認原發票，待對帳）")
    # **每個元素都要能驗**：以推導式配 isinstance 過濾會把 null／非 dict 靜默丟掉，
    # 於是 [{正確}, null] 仍得到 {expected} 而放行——遇平台回應損壞或 schema 漂移時，
    # 未知品項等於被無視（Codex 第二輪）。改為逐一要求合法字軌，任何未知元素一律拋。
    for item in items:
        if not isinstance(item, dict):
            raise AmegoTransportError(
                "allowance_query 的 product_item 含非物件元素（回應不可信），待對帳"
            )
        number = str(item.get("original_invoice_number") or "")
        if not _INVOICE_NO_RE.fullmatch(number):
            raise AmegoTransportError(
                "allowance_query 的 product_item 缺合法原發票字軌（回應不可信），待對帳"
            )
        if number != expected:
            raise AmegoTransportError(
                f"allowance_query 查到的原發票 {number} 與本地 {expected} 不符"
                "——該折讓可能不是本筆（單號重號），待人工對帳"
            )


def parse_query_allowance_exists(
    resp: dict[str, object],
    *,
    expect_original_invoice_no: str,
    expect_net: Decimal,
    expect_tax: Decimal,
    expect_not_before: datetime,
) -> bool:
    """allowance_query → 平台是否已有此折讓單（G0401 對帳，Codex 第七輪）。

    True＝已開立（D0401/B0401）→ 補記成功、不重送；False＝明確查無（code=71）→ 可送；
    其餘（錯誤碼、已作廢 D0501/B0501——本系統未送過 g0501、屬異常、曖昧欄位）→
    AmegoTransportError 待對帳。
    """
    code = resp.get("code")
    if type(code) is not int:
        raise AmegoTransportError("allowance_query 回應 code 型別不明（結果不可信，待對帳）")
    if code == _QUERY_NOT_FOUND_CODE:
        return False
    if code != 0:
        raise AmegoTransportError(
            f"allowance_query 回錯誤碼 {code}（非查無，不得據以重送；待對帳）"
        )
    data = resp.get("data")
    invoice_type = str(data.get("invoice_type") or "") if isinstance(data, dict) else ""
    if invoice_type in _ALLOWANCE_TYPE_ISSUED:
        assert isinstance(data, dict)  # invoice_type 非空即代表 data 是 dict
        # 折讓的平台 total_amount 為**未稅**、tax_amount 為稅額（對真 Amego 實測確認）
        _assert_same_record(
            _platform_amount(data, "total_amount", ctx="allowance_query"),
            expect_net,
            ctx="allowance_query",
            label="未稅金額",
        )
        _assert_same_record(
            _platform_amount(data, "tax_amount", ctx="allowance_query"),
            expect_tax,
            ctx="allowance_query",
            label="稅額",
        )
        _assert_allowance_original_invoice(data, expect_original_invoice_no)
        _assert_created_after(data, expect_not_before, ctx="allowance_query")
        _assert_platform_status_ok(data, ctx="allowance_query")
        # 與作廢 parser 對稱：**先蒐集全部 wait 事實再決策**。除了本折讓的待作廢
        # （D0501/B0501），**原發票的待作廢／註銷（C0501/A0501、C0701/A0701）同樣相斥**
        # ——原發票若被作廢，掛在它底下的折讓就不成立，本地卻會永久留著 ALLOWANCE。
        pending = {
            str(w.get("invoice_type") or "") for w in _wait_entries(data, ctx="allowance_query")
        }
        conflicting = pending & (
            _ALLOWANCE_TYPE_VOIDED | _INVOICE_TYPE_VOIDED | _INVOICE_TYPE_CANCELLED
        )
        if conflicting:
            raise AmegoTransportError(
                f"allowance_query 查到的紀錄另掛待處理的 {sorted(conflicting)}"
                "——與本次折讓相斥，待人工對帳"
            )
        return True
    raise AmegoTransportError(
        f"allowance_query 回不明 invoice_type「{invoice_type}」（結果不可信，待對帳）"
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
        except (ValueError, RecursionError) as exc:
            # RecursionError：極深巢狀的合法 JSON 會讓 stdlib 解析器爆堆疊。若發生在 POST
            # 回應上，平台可能已收件，卻因例外越過受控邊界而讓佇列空白卡死（Codex 第六輪）。
            raise AmegoTransportError("Amego API 回應無法解析（非 JSON 或巢狀過深）") from exc
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
        if not re.fullmatch(r"\d{8}", seller_tax_id.strip() or ""):
            raise AmegoNotConfigured(
                "店家統編未設定或格式不符（stores.tax_id 須為 8 碼數字），不可呼叫 Amego API"
            )
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
